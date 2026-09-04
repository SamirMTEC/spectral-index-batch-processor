"""Auth0 access-token validation for production API requests."""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient, PyJWTError
from spectral_common import Settings


class InvalidAccessTokenError(ValueError):
    """Raised when an access token cannot establish a trusted identity."""


@lru_cache(maxsize=4)
def _jwks_client(issuer: str) -> PyJWKClient:
    return PyJWKClient(
        f"{issuer}.well-known/jwks.json",
        cache_keys=True,
        cache_jwk_set=True,
        lifespan=300,
        timeout=5,
    )


def _decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    issuer = settings.auth0_issuer
    audience = settings.auth0_audience
    if issuer is None or audience is None:
        raise InvalidAccessTokenError("Auth0 is not configured")
    try:
        signing_key = _jwks_client(issuer).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iat", "iss", "sub", "aud"]},
        )
    except (PyJWTError, ValueError) as error:
        raise InvalidAccessTokenError("Access token is invalid") from error
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip() or len(subject) > 512:
        raise InvalidAccessTokenError("Access token subject is invalid")
    return claims


async def validate_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Validate a JWT without blocking the async request loop during JWKS I/O."""

    return await asyncio.to_thread(_decode_access_token, token, settings)
