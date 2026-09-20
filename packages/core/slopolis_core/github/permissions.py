"""Which GitHub App permissions publishing a review needs (spec 10.3).

Publishing writes two surfaces (spec 10.7): inline review comments and the
rolling summary comment, both of which GitHub accepts with ``pull_requests:
write`` — a PR conversation comment is an issue comment, so no Issues scope is
involved. The check run is the third write, but it is advisory (overview §10)
and posted last: without ``checks: write`` the review still reaches the pull
request as a comment, so a missing Checks grant is a notice rather than a
refusal.

Reading a pull request needs none of these, so an installation can pass every
other check and still be unable to post the review. This module is the single
place that says which scopes that takes, which of them are merely advisory, and
whether a permission set grants them, shared by pre-flight (which refuses the
submission or notices the loss) and anything that must name the same scopes.
"""

from __future__ import annotations

from collections.abc import Mapping

from slopolis_core.github._mapping import permission_satisfies

__all__ = [
    "PUBLISH_SCOPES",
    "PUBLISH_SCOPE_LABELS",
    "REQUIRED_PUBLISH_SCOPES",
    "missing_optional_scopes",
    "missing_required_scopes",
]

#: Every permission publishing touches, in the order GitHub's App settings list
#: them and the refusal and the notice name them.
PUBLISH_SCOPES: tuple[str, ...] = ("pull_requests", "checks")

#: The scopes publishing cannot post the review without. Both comment kinds need
#: ``pull_requests: write``; the rest of :data:`PUBLISH_SCOPES` is advisory, so
#: pre-flight notices it instead of refusing. A ``frozenset`` because only
#: membership matters: the order the answers read in comes from
#: :data:`PUBLISH_SCOPES`, so refusal and notice wording stay stable together.
REQUIRED_PUBLISH_SCOPES: frozenset[str] = frozenset({"pull_requests"})

#: How each scope is labelled in GitHub's App settings, so a refusal can point
#: at the exact row to change and a notice can name what the check run is
#: missing.
PUBLISH_SCOPE_LABELS: Mapping[str, str] = {
    "pull_requests": "Pull requests",
    "checks": "Checks",
}


def _missing(permissions: Mapping[str, str]) -> list[str]:
    """Return the publish scopes ``permissions`` does not grant at ``write``.

    Keys are GitHub's permission names and values its levels; a scope the
    installation was not granted is simply absent, which counts as missing. The
    result follows :data:`PUBLISH_SCOPES` order, so two installations missing the
    same scopes refuse — or are notified — with the same wording.
    """
    return [
        scope
        for scope in PUBLISH_SCOPES
        if not permission_satisfies(permissions.get(scope, "none"), required="write")
    ]


def missing_required_scopes(permissions: Mapping[str, str]) -> list[str]:
    """Return the missing scopes publishing cannot post the review without.

    Split from :func:`missing_optional_scopes` because the two answers have
    different consequences — a refusal versus a notice — and a caller that reads
    the wrong one either blocks a publishable submission or accepts an
    unpublishable one. Naming the question in the call site keeps that choice
    visible; the ``write`` level and the ordering live in one place for both.
    """
    return [scope for scope in _missing(permissions) if scope in REQUIRED_PUBLISH_SCOPES]


def missing_optional_scopes(permissions: Mapping[str, str]) -> list[str]:
    """Return the missing scopes publishing can do without (spec 10.3).

    Today that is only the advisory check run, but the question is asked as
    "what will be skipped" rather than "is Checks missing", so adding another
    advisory write does not silently start refusing submissions.
    """
    return [scope for scope in _missing(permissions) if scope not in REQUIRED_PUBLISH_SCOPES]
