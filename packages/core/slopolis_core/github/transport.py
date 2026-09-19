"""Translate githubkit transport exceptions into slopolis's typed errors.

githubkit raises its own exception hierarchy on non-2xx responses (``RequestFailed``
and friends) *before* returning a ``Response``, so error status codes never reach
:func:`slopolis_core.github._mapping.raise_for_status`. Every REST method is
decorated with :func:`translate_errors` so callers only ever see
:class:`~slopolis_core.github.errors.GitHubError` subclasses.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable

import githubkit.exception

from slopolis_core.github.errors import (
    GitHubAuthError,
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitError,
)

__all__ = ["translate_errors"]


def translate_errors[**P, R](
    fn: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """Wrap an async GitHub call, mapping githubkit errors to typed ones.

    ``RateLimitExceeded`` and its subtypes become :class:`GitHubRateLimitError`;
    a 404 becomes :class:`GitHubNotFoundError`; 401/403 becomes
    :class:`GitHubAuthError`; credential/expiry and timeouts become the closest
    typed error. The method name is used as the failure description.
    """

    what = fn.__name__

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except githubkit.exception.RateLimitExceeded as exc:
            raise GitHubRateLimitError(f"GitHub rate limit hit during {what}") from exc
        except githubkit.exception.RequestFailed as exc:
            raise _from_request_failed(exc, what) from exc
        except githubkit.exception.AuthCredentialError as exc:
            raise GitHubAuthError(f"GitHub credentials invalid during {what}") from exc
        except githubkit.exception.AuthExpiredError as exc:
            raise GitHubAuthError(f"GitHub credentials expired during {what}") from exc
        except githubkit.exception.RequestTimeout as exc:
            raise GitHubError(f"GitHub request timed out during {what}") from exc
        except githubkit.exception.GitHubException as exc:
            raise GitHubError(f"GitHub request failed during {what}: {exc!r}") from exc

    return wrapper


def _from_request_failed(exc: githubkit.exception.RequestFailed, what: str) -> GitHubError:
    """Map a ``RequestFailed`` onto the status-appropriate typed error."""
    status = exc.response.status_code
    if status == 404:
        return GitHubNotFoundError(f"GitHub resource not found during {what}")
    if status in (401, 403):
        return GitHubAuthError(f"GitHub authorization failed during {what} (HTTP {status})")
    if status == 429:
        return GitHubRateLimitError(f"GitHub rate limit hit during {what}")
    return GitHubError(f"GitHub request failed during {what} (HTTP {status})")
