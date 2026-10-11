"""Supabase JWT authentication.

Tokens are verified against the project's JWKS (asymmetric signing keys) or,
for legacy projects, the shared HS256 secret. The role comes from the
`profiles` table, never from the token's user metadata.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status

from app.config import Settings, get_settings
from app.services.access import Actor
from app.store.base import Store, StoreError, one


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=600)


def decode_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "")
        if alg == "HS256":
            secret = settings.supabase_jwt_secret.get_secret_value()
            if not secret:
                raise jwt.InvalidTokenError("HS256 tokens are not accepted (no legacy secret configured)")
            return jwt.decode(token, secret, algorithms=["HS256"], audience=settings.jwt_audience)
        if not settings.supabase_jwks_url:
            raise jwt.InvalidTokenError("SUPABASE_JWKS_URL is not configured")
        key = _jwks_client(settings.supabase_jwks_url).get_signing_key_from_jwt(token)
        return jwt.decode(token, key.key, algorithms=["ES256", "RS256", "EdDSA"], audience=settings.jwt_audience)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {type(exc).__name__}") from None


def actor_from_claims(store: Store, claims: dict[str, Any]) -> Actor:
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has no subject")
    try:
        profile = one(store.select("profiles", eq={"id": user_id}, limit=1))
    except StoreError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Profile lookup failed") from None
    if not profile:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No profile for this user")
    return Actor(user_id=user_id, role=profile["role"], organisation_id=profile.get("organisation_id"))


def get_store_dep() -> Store:  # overridden in app.main
    raise NotImplementedError


def current_actor(
    request: Request,
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store_dep),
) -> Actor:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    claims = decode_token(auth.split(" ", 1)[1].strip(), settings)
    actor = actor_from_claims(store, claims)
    request.state.actor_key = actor.user_id
    return actor
