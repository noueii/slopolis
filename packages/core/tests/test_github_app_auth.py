"""App authentication: the RS256 JWT every GitHub App call is signed with.

`GitHubClient.from_app` (server startup) and the worker's installation-token
minting both go through githubkit's `AppAuth`, which signs with RS256. That
algorithm lives in PyJWT's `crypto` extra, so these tests fail if the extra is
dropped from `pyproject.toml` — whose real failure mode is a silently disabled
GitHub client, not an import error.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from githubkit import GitHub
from githubkit.auth import AppAuthStrategy
from githubkit.auth.app import AppAuth

#: githubkit caches one JWT per issuer in a process-wide cache, so each test
#: uses its own App id to get its own freshly signed token.
APP_ID = 5007508
OTHER_APP_ID = 5007509


def _auth(app_id: int) -> tuple[AppAuth, rsa.RSAPrivateKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    client = GitHub(AppAuthStrategy(app_id, pem))
    return AppAuth(github=client, app_id=app_id, private_key=pem), key


def _claims(token: str, key: rsa.RSAPrivateKey) -> dict[str, object]:
    return jwt.decode(
        token,
        key.public_key(),
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )


def test_app_auth_mints_a_verifiable_rs256_jwt() -> None:
    """The App id is the issuer, and the signature verifies with the App's key."""
    auth, key = _auth(APP_ID)

    token = auth.get_jwt()

    assert token.count(".") == 2
    assert _claims(token, key)["iss"] == str(APP_ID)


def test_expiry_claim_is_short_lived() -> None:
    """GitHub rejects JWTs older than 10 minutes; ours expire inside that window."""
    auth, key = _auth(OTHER_APP_ID)

    claims = _claims(auth.get_jwt(), key)

    issued = float(claims["iat"])  # type: ignore[arg-type]
    expires = float(claims["exp"])  # type: ignore[arg-type]
    now = datetime.now(UTC).timestamp()
    assert issued <= now
    assert 0 < expires - issued <= 10 * 60
    assert expires > now + timedelta(minutes=1).total_seconds()
