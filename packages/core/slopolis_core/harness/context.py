"""Ambient run identity, so a model call made deep inside a run is attributable.

A model call does not always originate in the tool-loop that owns it: the
reviewer run's single turn delegates to the review harness, which composes its
own prompt and calls the gateway itself. The runtime therefore publishes the
run it is executing — :func:`bind_run` around the loop — and whatever makes the
call reads it back with :func:`current_run` (spec v2 11.3).

The value lives in a :class:`~contextvars.ContextVar`, so it is per-task: a
spawned child rebinds it inside its own task and the parent's binding is
untouched when the child returns.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from slopolis_core.harness.types import HarnessLevel

__all__ = ["RunRef", "bind_run", "current_run"]


@dataclass(frozen=True, slots=True)
class RunRef:
    """One in-flight run's identity, as plain data.

    Deliberately not the run's row: this is what a model call needs to say which
    run it belongs to, and nothing more.
    """

    run_id: uuid.UUID
    session_id: uuid.UUID
    target_id: uuid.UUID | None
    level: HarnessLevel
    role: str
    model_id: str | None


_current: ContextVar[RunRef | None] = ContextVar("slopolis_current_run", default=None)


def current_run() -> RunRef | None:
    """Return the run this task is executing, or ``None`` outside one."""
    return _current.get()


@contextmanager
def bind_run(ref: RunRef) -> Generator[None]:
    """Publish ``ref`` as the current run for the duration of the block."""
    token = _current.set(ref)
    try:
        yield
    finally:
        _current.reset(token)
