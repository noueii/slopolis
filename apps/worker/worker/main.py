"""ARQ worker entrypoint for Slopolis (spec 10.5).

``WorkerSettings`` registers the single ``review_target`` job, points ARQ at the
shared Redis instance, and applies the worker tuning from :mod:`worker.config`.
The startup hook logs the build fingerprint of the code this process is running,
then builds the review context, database session factory, and the slot gate that
bounds how many targets of one repository (and installation) run at once; the
shutdown hook closes the model pools and disposes the engine.

The fingerprint exists because a worker keeps the code it started with: a job
enqueued by a newer server can arrive at a worker whose signature no longer
matches it, failing inside ARQ without ever touching the target — leaving it
``queued`` with no job, and a stack trace as the only clue. One line naming the
revision (or, without one, the newest mtime of these sources) is what lets an
operator compare a running worker with the checkout before trusting a stuck queue
to be a real failure.
"""

from __future__ import annotations

import datetime as dt
import logging
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import ClassVar, Required, TypedDict, cast

from arq.connections import ArqRedis, RedisSettings

from slopolis_core.llm.client import LiteLlmClient, LlmAuthError
from slopolis_core.settings import get_settings
from slopolis_db.session import get_engine
from worker.config import get_worker_config
from worker.deps import ReviewContext, SessionFactory, build_llm_client, db_session_factory
from worker.jobs.review_target import review_target
from worker.jobs.slots import SlotCounter, SlotGate, WorkspaceLimits

__all__ = ["StartupCtx", "WorkerSettings", "build_fingerprint", "on_shutdown", "on_startup"]

_LOG = logging.getLogger("worker.main")

_JobCoroutine = Callable[..., Awaitable[None]]

#: The directory git is asked for a revision: the worker app itself, so the
#: revision is this app's and not some other checkout's.
_WORKER_APP_DIR = Path(__file__).resolve().parent.parent

#: The package whose sources date the fallback fingerprint: the modules this
#: worker actually imports.
_WORKER_PACKAGE_DIR = Path(__file__).resolve().parent

#: Seconds the git lookup may take before the fingerprint falls back. A startup
#: hook must not hang on a repository that is slow or unreadable.
_GIT_TIMEOUT_S = 5.0

#: How the worker's own records are formatted: ARQ's shape (time first), with the
#: logger's name added so a line says which part of the worker wrote it.
_LOG_FORMAT = "%(asctime)s: %(levelname)s %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%H:%M:%S"


class StartupCtx(TypedDict, total=False):
    """The subset of the ARQ context the startup/shutdown hooks manage."""

    #: ARQ sets this before it calls the hook, so the gate can count on it.
    redis: Required[ArqRedis]
    review: ReviewContext
    session_factory: SessionFactory
    slots: SlotGate


def build_fingerprint() -> str:
    """Return a one-line identity for the code this worker process is running.

    The revision of the checkout it was started from when git can be asked,
    otherwise the newest mtime of the sources it is running — which is what an
    operator has left to compare against the checkout when the revision cannot be
    read (no git binary, or a copy deployed without one). It never fails: a
    worker that cannot name its revision still has reviews to run, so an
    unreadable revision is something to say rather than something to raise.
    """
    revision = _git_revision()
    if revision is not None:
        return revision
    newest = _newest_source_mtime()
    if newest is None:
        return "no git revision; no worker sources to date"
    return f"no git revision; worker sources dated {newest.isoformat(timespec='seconds')}"


def _git_revision() -> str | None:
    """Return the short revision of the checkout this code lives in, or ``None``."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_WORKER_APP_DIR,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # No git binary, or one that will not run: the fallback answers instead.
        return None
    revision = done.stdout.strip()
    if done.returncode != 0 or not revision:
        return None
    return revision


def _newest_source_mtime() -> dt.datetime | None:
    """Return the newest modification time among the worker's own sources."""
    newest: float | None = None
    for source in _WORKER_PACKAGE_DIR.rglob("*.py"):
        try:
            mtime = source.stat().st_mtime
        except OSError:
            continue
        if newest is None or mtime > newest:
            newest = mtime
    if newest is None:
        return None
    return dt.datetime.fromtimestamp(newest, tz=dt.UTC)


def _configure_logging() -> None:
    """Give the worker's own loggers a handler and a level, once, if nothing has.

    ARQ configures only its own ``arq`` logger and leaves the root alone, where
    the level stays ``WARNING``: every ``worker.*`` record below a warning — this
    startup fingerprint, and the per-target lines the job writes — is dropped
    before it is formatted, so the process that runs the reviews says nothing
    about them. A deployment that configured logging itself (handlers on the root,
    or on ``worker``) keeps its own choice: this only fills the silence.
    """
    worker_log = logging.getLogger("worker")
    if logging.getLogger().handlers or worker_log.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
    worker_log.addHandler(handler)
    worker_log.setLevel(logging.INFO)


async def on_startup(ctx: StartupCtx) -> None:
    """Say what code this is, then create the review context, sessions, and gate."""
    _configure_logging()
    # The one line an operator needs to tell a worker apart from the checkout it was
    # started from — logged before anything else, because the jobs it will run are
    # what is otherwise indistinguishable when the two have drifted.
    _LOG.info("worker build fingerprint: %s", build_fingerprint())
    config = get_worker_config()
    ctx["review"] = ReviewContext(llm=_open_gateway(), config=config)
    ctx["session_factory"] = db_session_factory()
    # ARQ hands the job context its Redis pool. redis-py types commands as a
    # sync/async union, so the awaitable subset the gate uses is declared as its
    # own protocol (see ``worker.jobs.slots``).
    ctx["slots"] = SlotGate(
        cast("SlotCounter", ctx["redis"]), limits=WorkspaceLimits(), config=config
    )


def _open_gateway() -> LiteLlmClient | None:
    """Build the process-level gateway client, or ``None`` without a master key.

    The gateway is only the fallback for a model whose workspace credential is
    unusable, so a worker whose models are all credential-linked must boot without
    one — refusing to start here would take the primary path down with the
    fallback. Same shape as the server's ``main._open_llm``: one warning line
    naming the consequence, and a job that needs a gateway says so per target.
    """
    try:
        return build_llm_client()
    except LlmAuthError as exc:
        _LOG.warning("Model gateway is not configured: %s", exc)
        return None


async def on_shutdown(ctx: StartupCtx) -> None:
    """Close the model clients this worker opened and dispose the database engine."""
    review = ctx.get("review")
    if review is not None:
        await review.aclose()
    await get_engine().dispose()


class WorkerSettings:
    """ARQ worker configuration: one review job per PR target."""

    functions: ClassVar[list[_JobCoroutine]] = [review_target]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = get_worker_config().max_jobs
    job_timeout = get_worker_config().job_timeout_s
    max_tries = get_worker_config().max_tries
    on_startup = on_startup
    on_shutdown = on_shutdown
