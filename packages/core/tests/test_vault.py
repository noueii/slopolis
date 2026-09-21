"""Tests for the workspace secret vault (spec 10.2 §Vault)."""

import base64

import pytest

from slopolis_core.settings import Settings
from slopolis_core.vault import (
    SecretVault,
    VaultDecryptError,
    VaultError,
    VaultNotConfigured,
)

_MATERIAL = b"unit-test-master-key-material-32b!"
_BASE64 = base64.b64encode(_MATERIAL).decode()
# 35 bytes of hex: 70 characters is not valid base64, so this exercises the hex
# decoder instead of the base64 one that runs first.
_HEX = bytes(range(35)).hex()
_RAW = "raw-material-" + "x" * 30

# Blob slices: version(1) | wrap nonce(12) | wrapped data key(48) | data nonce(12).
_WRAPPED_KEY = slice(13, 61)
_DATA_NONCE = slice(61, 73)


def _vault() -> SecretVault:
    """Return a vault built from base64 material."""
    return SecretVault.from_env(_BASE64)


def test_round_trip() -> None:
    """Given a sealed secret, opening it returns the original text."""
    vault = _vault()

    blob = vault.seal("sk-live-1234567890")

    assert vault.open(blob) == "sk-live-1234567890"


def test_layout_is_versioned_with_a_wrapped_key() -> None:
    """Given a sealed secret, the blob carries the version byte and both nonces."""
    blob = _vault().seal("sk-live-1234567890")

    assert blob[0] == 0x01
    # version + wrap nonce + wrapped data key + data nonce + GCM tag + plaintext
    assert len(blob) == 1 + 12 + 48 + 12 + 16 + len("sk-live-1234567890")


def test_two_seals_of_the_same_plaintext_differ() -> None:
    """Given one plaintext, sealing twice yields different blobs that both open."""
    vault = _vault()

    first = vault.seal("sk-live-1234567890")
    second = vault.seal("sk-live-1234567890")

    assert first != second
    assert vault.open(first) == vault.open(second) == "sk-live-1234567890"


def test_a_flipped_ciphertext_byte_fails_to_open() -> None:
    """Given a tampered ciphertext byte, opening raises VaultDecryptError."""
    vault = _vault()
    blob = bytearray(vault.seal("sk-live-1234567890"))
    blob[-1] ^= 0x01

    with pytest.raises(VaultDecryptError):
        vault.open(bytes(blob))


def test_a_swapped_nonce_fails_to_open() -> None:
    """Given two blobs, grafting one's data nonce onto the other fails."""
    vault = _vault()
    first = bytearray(vault.seal("sk-live-1234567890"))
    second = vault.seal("sk-test-0987654321")
    first[_DATA_NONCE] = second[_DATA_NONCE]

    with pytest.raises(VaultDecryptError):
        vault.open(bytes(first))


def test_a_swapped_wrapped_key_fails_to_open() -> None:
    """Given two blobs, grafting one's wrapped data key onto the other fails."""
    vault = _vault()
    first = bytearray(vault.seal("sk-live-1234567890"))
    second = vault.seal("sk-test-0987654321")
    first[_WRAPPED_KEY] = second[_WRAPPED_KEY]

    with pytest.raises(VaultDecryptError):
        vault.open(bytes(first))


def test_a_blob_sealed_with_another_key_fails_to_open() -> None:
    """Given a blob sealed under key A, key B cannot open it."""
    a = SecretVault.from_env(base64.b64encode(b"a" * 32).decode())
    b = SecretVault.from_env(base64.b64encode(b"b" * 32).decode())
    blob = a.seal("sk-live-1234567890")

    with pytest.raises(VaultDecryptError):
        b.open(blob)


def test_a_truncated_or_mislabelled_blob_raises() -> None:
    """Given a short or unversioned blob, opening raises VaultDecryptError."""
    vault = _vault()

    with pytest.raises(VaultDecryptError):
        vault.open(b"\x01short")
    with pytest.raises(VaultDecryptError):
        vault.open(b"\x02" + vault.seal("sk-live-1234567890")[1:])
    with pytest.raises(VaultDecryptError):
        vault.open(b"")


def test_an_empty_secret_is_refused() -> None:
    """Given an empty plaintext, sealing raises VaultError."""
    with pytest.raises(VaultError):
        _vault().seal("")


def test_last4() -> None:
    """Given a key, last4 returns its trailing four characters."""
    assert SecretVault.last4("sk-live-1234567890") == "7890"
    assert SecretVault.last4("abc") == "abc"


def test_material_forms_all_work() -> None:
    """Given base64, hex, or raw material, a vault seals and opens."""
    for raw in (_BASE64, _HEX, _RAW):
        vault = SecretVault.from_env(raw)
        assert vault.open(vault.seal("sk-live-1234567890")) == "sk-live-1234567890"


def test_the_same_material_derives_the_same_key() -> None:
    """Given the same material twice, one vault opens what the other sealed."""
    sealed = SecretVault.from_env(_BASE64).seal("sk-live-1234567890")

    assert SecretVault.from_env(_BASE64).open(sealed) == "sk-live-1234567890"


def test_missing_blank_or_short_material_is_not_configured() -> None:
    """Given no key, a blank key, or under 32 bytes, configuration is refused."""
    for raw in (None, "", "   ", base64.b64encode(b"short").decode(), "abc", "y" * 31):
        with pytest.raises(VaultNotConfigured):
            SecretVault.from_env(raw)


def test_from_settings_reads_encryption_key() -> None:
    """Given Settings carrying ENCRYPTION_KEY, from_settings builds a vault."""
    settings = Settings.model_construct(encryption_key=_BASE64)

    vault = SecretVault.from_settings(settings)

    assert vault.open(vault.seal("sk-live-1234567890")) == "sk-live-1234567890"

    with pytest.raises(VaultNotConfigured):
        SecretVault.from_settings(Settings.model_construct(encryption_key=None))


def test_a_master_key_must_be_thirty_two_bytes() -> None:
    """Given a wrongly sized master key, constructing a vault raises."""
    with pytest.raises(VaultError):
        SecretVault(b"too-short")

    SecretVault(bytes(32))
