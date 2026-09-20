"""ARQ worker entrypoint for Slopolis (spec 10.5).

``WorkerSettings`` registers the single ``review_target`` job, points ARQ at the
shared Redis instance, and applies the worker tuning from :mod:`worker.config`.
The startup hook builds the review context, database session factory, and the
slot gate that bounds how many targets of one repository (and installation) run
at once; the shutdown hook closes the model pools and disposes the engine.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import ClassVar, Required, TypedDict, cast

from arq.connections import ArqRedis, RedisSettings

from slopolis_core.llm.client import LiteLlmClient, LlmAuthError
from slopolis_core.settings import get_settings
from slopolis_db.session import get_engine
from worker.config import get_worker_config
from worker.deps import ReviewContext, SessionFactory, build_llm_client, db_session_factory
from worker.jobs.review_target import review_target
from worker.jobs.slots import SlotCounter, SlotGate, WorkspaceLimits

__all__ = ["StartupCtx", "WorkerSettings", "on_shutdown", "on_startup"]

_LOG = logging.getLogger("worker.main")

_JobCoroutine = Callable[..., Awaitable[None]]


class StartupCtx(TypedDict, total=False):
    """The subset of the ARQ context the startup/shutdown hooks manage."""

    #: ARQ sets this before it calls the hook, so the gate can count on it.
    redis: Required[ArqRedis]
    review: ReviewContext
    session_factory: SessionFactory
    slots: SlotGate


async def on_startup(ctx: StartupCtx) -> None:
    """Create the review context, database session factory, and slot gate."""
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
