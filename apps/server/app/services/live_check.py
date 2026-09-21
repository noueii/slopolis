"""Pre-flight's live model check, run on the workspace's own credential (spec 10.2).

A pre-flight live check is one model call, and spec 10.2 §*Which credential a
model call uses* applies to it as much as to the worker's review: the completion
goes to the base URL and key of the credential **linked to the model it is
about**, opened from the vault the *Test connection* button sealed it with. That
is what makes a passing test mean something — same credential, same base URL —
instead of a submission that passes the screen and then fails on the gateway.

The process-level gateway client the app opened at boot is the fallback for a
workspace whose model has no usable credential, i.e. the self-hosted "one shared
gateway" deployment. With neither, the check fails naming the model and both ways
out, which pre-flight renders as a notice: the model call is one more thing to
fix, not a reason to lose the rest of what pre-flight found (spec 10.3).

Clients are pooled per credential. A check runs on every submission and the
credential behind a model rarely changes, so keeping the sockets and TLS
handshakes is worth it — but not without limit: the pool keeps the most recently
created clients, closes the ones it drops, and the app closes the rest at
shutdown.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from slopolis_core.llm.client import LiteLlmClient, LlmClient, LlmError
from slopolis_core.llm.models import ChatMessage
from slopolis_core.vault import VaultDecryptError

__all__ = [
    "ClientFactory",
    "CredentialClientPool",
    "ManagedLlmClient",
    "ModelCredential",
    "ModelCredentialSource",
    "WorkspaceLiveModelCheck",
    "no_way_to_call",
]

_logger = logging.getLogger(__name__)

#: How many credential clients one process keeps. A workspace uses one or two
#: models at a time, so this covers the concurrent case and still bounds a
#: process that sees many credentials over its life.
_POOL_SIZE = 8


class ManagedLlmClient(LlmClient, Protocol):
    """A chat client the pool owns, so it can also be closed."""

    async def aclose(self) -> None: ...


#: Builds a chat client for one credential: its base URL and its decrypted key.
ClientFactory = Callable[[str, str], ManagedLlmClient]


def build_litellm_client(base_url: str, api_key: str) -> ManagedLlmClient:
    """Build the production client for a credential's endpoint (spec 10.2)."""
    return LiteLlmClient(base_url, api_key)


@dataclass(frozen=True, slots=True)
class ModelCredential:
    """The credential a model call runs on: its base URL and its decrypted key.

    The key is handed to a client constructor and nowhere else — it never reaches
    a log, a message, or a response. ``key_last4`` is the public half of the key
    (the same characters the settings screen shows), which lets the pool tell a
    rotated key from the one it already holds.
    """

    base_url: str
    api_key: str
    key_last4: str


@runtime_checkable
class ModelCredentialSource(Protocol):
    """Which credential serves a model (spec 10.2).

    The consumer-side port for this module: ``app.adapters.workspace`` answers it
    from the workspace's model catalog and credential rows, and tests answer it
    with a credential of their own.
    """

    async def credential_for_model(self, model_id: str) -> ModelCredential | None: ...


class CredentialClientPool:
    """At most ``max_size`` chat clients, one per credential, closed when dropped.

    Clients are identified by their credential's base URL, public last4, and a
    digest of the key: a key rotated to different characters — even to the same
    last four — is a new client rather than a stale one still sending the old key.
    """

    def __init__(
        self,
        factory: ClientFactory = build_litellm_client,
        *,
        max_size: int = _POOL_SIZE,
    ) -> None:
        self._factory = factory
        self._max_size = max_size
        self._clients: dict[tuple[str, str, str], ManagedLlmClient] = {}

    async def client_for(self, credential: ModelCredential) -> ManagedLlmClient:
        """Return the client for ``credential``, building and pooling one if needed."""
        key = _client_key(credential)
        client = self._clients.get(key)
        if client is None:
            client = self._factory(credential.base_url, credential.api_key)
            self._clients[key] = client
            await self._trim()
        return client

    async def aclose(self) -> None:
        """Close every pooled client; the app calls this at shutdown."""
        pooled = list(self._clients.values())
        self._clients.clear()
        for client in pooled:
            await _close_quietly(client)

    async def _trim(self) -> None:
        """Close the oldest clients past the bound, oldest first."""
        while len(self._clients) > self._max_size:
            oldest = next(iter(self._clients))
            await _close_quietly(self._clients.pop(oldest))


def no_way_to_call(model: str) -> str:
    """The failure that names the model and both ways to make it callable (spec 10.2)."""
    return (
        f"model {model} has no usable credential and the model gateway is not "
        f"configured; link a credential to {model} or set LITELLM_BASE_URL and "
        f"LITELLM_MASTER_KEY on the server"
    )


class WorkspaceLiveModelCheck:
    """``LiveModelCheck`` that runs one token on the model's own credential.

    Built per request because the workspace it asks is request-scoped; the
    clients it uses are not, which is why the pool is passed in and owned by the
    app.
    """

    def __init__(
        self,
        workspace: ModelCredentialSource,
        *,
        clients: CredentialClientPool,
        fallback: LlmClient | None = None,
    ) -> None:
        """Take the workspace to ask, the client pool, and the gateway fallback.

        ``fallback`` is the process-level gateway client the app opened at boot
        (``app.state.llm_client``), or ``None`` on a deployment that has no
        gateway. It stays the app's to close; this check only calls it.
        """
        self._workspace = workspace
        self._clients = clients
        self._fallback = fallback

    async def check(self, model: str) -> None:
        """Run a ``ping`` completion for ``model``, raising :class:`LlmError` on failure.

        Every reason the call cannot be made lands here as an ``LlmError`` naming
        the model, so pre-flight reports it as a notice rather than failing the
        request. That covers no credential and no gateway, and a key this process
        cannot open — a configuration error (spec 10.2) that must never become a
        silently skipped check.
        """
        try:
            credential = await self._workspace.credential_for_model(model)
        except VaultDecryptError as exc:
            raise LlmError(
                f"the stored credential for {model} cannot be decrypted with this "
                "process's ENCRYPTION_KEY; re-save the key in settings"
            ) from exc

        if credential is None:
            if self._fallback is None:
                raise LlmError(no_way_to_call(model))
            client: LlmClient = self._fallback
        else:
            client = await self._clients.client_for(credential)

        await client.complete(
            [ChatMessage(role="user", content="ping")],
            model=model,
            max_tokens=1,
        )


def _client_key(credential: ModelCredential) -> tuple[str, str, str]:
    """Identify a credential's client without putting its key in the key.

    The digest names the exact key, so rotating a key to another one with the
    same last four characters still builds a new client instead of reusing a
    client that would authenticate with the revoked key.
    """
    digest = hashlib.sha256(credential.api_key.encode("utf-8")).hexdigest()
    return (credential.base_url, credential.key_last4, digest)


async def _close_quietly(client: ManagedLlmClient) -> None:
    """Close a client this pool dropped, without failing the check or the shutdown.

    A pool that could not shrink would leak sockets; a close raising out of a
    check would turn a model call that succeeded into a failure.
    """
    try:
        await client.aclose()
    except Exception:
        _logger.warning("A pooled model client could not be closed", exc_info=True)
