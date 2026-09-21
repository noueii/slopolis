"""Worker failure vocabulary shared by the job modules.

``PermanentTargetError`` is the one failure a retry cannot fix: the target's own
configuration is wrong (an unparseable ``.codereview.yml``, a model that neither a
workspace credential nor the process gateway can serve). Retrying burns attempts
against a target that cannot start, so the job fails it on the spot.

``UnknownModeError`` is the job being handed a ``mode`` this build does not know:
the caller and the worker disagree about what the job was for, which is a bug
between two deployments rather than a property of the target. It says which mode
and which target instead of letting the job fall through to a review nobody asked
for — or, once the parameter is not in the signature at all, instead of the bare
``TypeError`` ARQ would report.

Both live apart from the modules that raise them — the repo-config loader and the
credential resolution — and apart from the job that catches them, so none of them
has to import another to share one exception type.
"""

from __future__ import annotations

__all__ = ["PermanentTargetError", "UnknownModeError"]


class PermanentTargetError(RuntimeError):
    """A failure that cannot succeed on retry (e.g. a model nothing can serve)."""


class UnknownModeError(ValueError):
    """A ``mode`` this worker does not define for the job it was handed."""
