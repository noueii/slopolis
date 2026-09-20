"""Worker failure vocabulary shared by the job modules.

``PermanentTargetError`` is the one failure a retry cannot fix: the target's own
configuration is wrong (an unparseable ``.codereview.yml``, a model that neither a
workspace credential nor the process gateway can serve). Retrying burns attempts
against a target that cannot start, so the job fails it on the spot.

It lives apart from the modules that raise it — the repo-config loader and the
credential resolution — and apart from the job that catches it, so none of them
has to import another to share one exception type.
"""

from __future__ import annotations

__all__ = ["PermanentTargetError"]


class PermanentTargetError(RuntimeError):
    """A failure that cannot succeed on retry (e.g. a model nothing can serve)."""
