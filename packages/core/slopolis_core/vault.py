"""AES-256-GCM envelope encryption for workspace secrets (spec 10.2).

The vault is the only code that ever sees a BYOK key in the clear. Every secret
is sealed under its own random 32-byte data key; the data key is wrapped by the
master key and stored beside the ciphertext. That keeps a plaintext from ever
reusing a nonce, makes two seals of the same secret differ, and makes a blob fail
to open when its pieces are swapped with another blob's.

Blob layout (one ``bytea`` column, versioned so a later scheme can migrate)::

    0x01 | wrap_nonce(12) | wrapped_dek(48) | data_nonce(12) | ciphertext

The master key comes from ``ENCRYPTION_KEY``. Accepted forms, tried in order —
the first one that decodes to at least 32 bytes wins:

1. base64 (standard alphabet, optional padding),
2. hex,
3. the string's own UTF-8 bytes.

Whatever the form, the decoded material is stretched into a 32-byte working key
with HKDF-SHA256 (fixed salt and info), so any sufficiently long secret works and
every process derives the same key. Material that is missing, blank, or shorter
than 32 bytes raises :class:`VaultNotConfigured` — a typed configuration error,
never a silently weakened key.
"""

from __future__ import annotations

import base64
import os
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from slopolis_core.settings import Settings, get_settings

__all__ = ["SecretVault", "VaultDecryptError", "VaultError", "VaultNotConfigured"]

_BLOB_VERSION: Final = 0x01
_VERSION_BYTES: Final = bytes([_BLOB_VERSION])
_NONCE_BYTES: Final = 12
_KEY_BYTES: Final = 32
_WRAPPED_KEY_BYTES: Final = 48  # the 32-byte data key plus its 16-byte GCM tag
_WRAP_OFFSET: Final = 1 + _NONCE_BYTES
_DATA_NONCE_OFFSET: Final = _WRAP_OFFSET + _WRAPPED_KEY_BYTES
_CIPHERTEXT_OFFSET: Final = _DATA_NONCE_OFFSET + _NONCE_BYTES
_MIN_BLOB_BYTES: Final = _CIPHERTEXT_OFFSET + 16

#: AAD binding the wrapped data key to this layout, so a wrapped key cannot be
#: moved between schemes or blobs.
_WRAP_AAD: Final = b"slopolis.vault.v1"
_HKDF_SALT: Final = b"slopolis.vault.v1.hkdf-salt"
_HKDF_INFO: Final = b"slopolis.vault.v1.master-key"


class VaultError(Exception):
    """Base class for vault configuration and sealing failures."""


class VaultNotConfigured(VaultError):
    """``ENCRYPTION_KEY`` is absent or too short to derive a master key."""


class VaultDecryptError(VaultError):
    """A secret blob could not be decrypted with this master key."""


class SecretVault:
    """Seals and opens workspace secrets under one 32-byte master key."""

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != _KEY_BYTES:
            raise VaultError("the vault master key must be exactly 32 bytes")
        self._master_key = master_key

    @classmethod
    def from_env(cls, raw: str | None) -> SecretVault:
        """Build a vault from ``ENCRYPTION_KEY`` material, or raise.

        Raises :class:`VaultNotConfigured` when the value is missing, blank, or
        decodes to fewer than 32 bytes.
        """
        material = _decode_material(raw)
        if material is None or len(material) < _KEY_BYTES:
            raise VaultNotConfigured(
                "ENCRYPTION_KEY must provide at least 32 bytes of material"
            )
        return cls(_derive_key(material))

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> SecretVault:
        """Build a vault from process settings (``settings.encryption_key``)."""
        resolved = settings if settings is not None else get_settings()
        return cls.from_env(resolved.encryption_key)

    def seal(self, plaintext: str) -> bytes:
        """Encrypt ``plaintext`` under a fresh data key and return the blob."""
        if not plaintext:
            raise VaultError("refusing to seal an empty secret")

        data_key = os.urandom(_KEY_BYTES)
        wrap_nonce = os.urandom(_NONCE_BYTES)
        wrapped_key = AESGCM(self._master_key).encrypt(wrap_nonce, data_key, _WRAP_AAD)
        data_nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(data_key).encrypt(
            data_nonce, plaintext.encode("utf-8"), None
        )
        return bytes([_BLOB_VERSION]) + wrap_nonce + wrapped_key + data_nonce + ciphertext

    def open(self, blob: bytes) -> str:
        """Decrypt a blob, raising :class:`VaultDecryptError` when it does not open.

        A wrong master key, a tampered byte, a truncated blob, and pieces swapped
        between two blobs all land here: the API answers 503 rather than treating
        an undecryptable credential as an authentication failure.
        """
        if len(blob) < _MIN_BLOB_BYTES or blob[:1] != _VERSION_BYTES:
            raise VaultDecryptError("the secret blob is malformed")

        wrap_nonce = blob[1:_WRAP_OFFSET]
        wrapped_key = blob[_WRAP_OFFSET:_DATA_NONCE_OFFSET]
        data_nonce = blob[_DATA_NONCE_OFFSET:_CIPHERTEXT_OFFSET]
        ciphertext = blob[_CIPHERTEXT_OFFSET:]
        try:
            data_key = AESGCM(self._master_key).decrypt(
                wrap_nonce, wrapped_key, _WRAP_AAD
            )
            plaintext = AESGCM(data_key).decrypt(data_nonce, ciphertext, None)
        except (InvalidTag, ValueError) as exc:
            # Never echo the input: the error text is shown to an operator, and a
            # blob is a secret too.
            raise VaultDecryptError("the secret could not be decrypted") from exc
        return plaintext.decode("utf-8")

    @staticmethod
    def last4(plaintext: str) -> str:
        """Return the trailing characters the API exposes as ``keyLast4``."""
        return plaintext[-4:]


def _decode_material(raw: str | None) -> bytes | None:
    """Decode ``ENCRYPTION_KEY``, or ``None`` when nothing usable is set."""
    if raw is None:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    for decode in (_from_base64, _from_hex):
        decoded = decode(candidate)
        if decoded is not None and len(decoded) >= _KEY_BYTES:
            return decoded
    return candidate.encode("utf-8")


def _from_base64(candidate: str) -> bytes | None:
    """Decode base64 material, or ``None`` when it is not base64."""
    try:
        return base64.b64decode(candidate, validate=True)
    except ValueError:
        return None


def _from_hex(candidate: str) -> bytes | None:
    """Decode hex material, or ``None`` when it is not hex."""
    try:
        return bytes.fromhex(candidate)
    except ValueError:
        return None


def _derive_key(material: bytes) -> bytes:
    """Stretch arbitrary-length material into a 32-byte AES-256 key."""
    return HKDF(
        algorithm=SHA256(),
        length=_KEY_BYTES,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    ).derive(material)
