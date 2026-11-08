"""Google identity: verify ID tokens from sign-in, and mint fresh access tokens from stored refresh tokens."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

import httpx

from app.config import get_settings

log = logging.getLogger("api.google")

BQ_SCOPE = "https://www.googleapis.com/auth/bigquery"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleAuthError(Exception):
    """Sign-in or token refresh rejected by Google (or misconfigured)."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    email_verified: bool
    hosted_domain: str | None  # `hd`: set only for Google Workspace accounts (Google-verified domain)
    name: str | None
    picture: str | None


def _verify_sync(id_token: str, client_id: str) -> dict:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    # checks signature (Google certs), expiry, issuer and audience
    return google_id_token.verify_oauth2_token(id_token, google_requests.Request(), audience=client_id,
                                               clock_skew_in_seconds=10)


async def verify_id_token(id_token: str) -> GoogleIdentity:
    client_id = get_settings().google_client_id
    if not client_id:
        raise GoogleAuthError("GOOGLE_CLIENT_ID is not configured on the API")
    try:
        claims = await asyncio.to_thread(_verify_sync, id_token, client_id)
    except ValueError as e:
        raise GoogleAuthError(f"invalid Google ID token: {e}") from e
    if not claims.get("email") or not claims.get("email_verified"):
        raise GoogleAuthError("Google account has no verified email")
    return GoogleIdentity(
        sub=str(claims["sub"]), email=claims["email"].lower(), email_verified=True,
        hosted_domain=(claims.get("hd") or None), name=claims.get("name"), picture=claims.get("picture"),
    )


# ---------------------------------------------------------------- access tokens (cached per user)
_cache: dict[uuid.UUID, tuple[str, float]] = {}
_locks: dict[uuid.UUID, asyncio.Lock] = {}


async def access_token_for(user_id: uuid.UUID, refresh_token: str, *, client: httpx.AsyncClient | None = None) -> str:
    """A valid Google access token for this user (refreshed when < 2 min left). Never logged."""
    cached = _cache.get(user_id)
    if cached and cached[1] - time.time() > 120:
        return cached[0]
    lock = _locks.setdefault(user_id, asyncio.Lock())
    async with lock:  # one refresh per user at a time
        cached = _cache.get(user_id)
        if cached and cached[1] - time.time() > 120:
            return cached[0]
        s = get_settings()
        if not (s.google_client_id and s.google_client_secret):
            raise GoogleAuthError("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not configured on the API")
        data = {"grant_type": "refresh_token", "refresh_token": refresh_token,
                "client_id": s.google_client_id, "client_secret": s.google_client_secret}
        own = client is None
        client = client or httpx.AsyncClient(timeout=10)
        try:
            r = await client.post(TOKEN_URL, data=data)
        finally:
            if own:
                await client.aclose()
        if r.status_code != 200:
            err = (r.json() if r.headers.get("content-type", "").startswith("application/json") else {}).get("error")
            log.warning("google token refresh failed", extra={"user_id": str(user_id), "status": r.status_code,
                                                              "google_error": err})
            _cache.pop(user_id, None)
            raise GoogleAuthError("invalid_grant" if err == "invalid_grant" else f"token refresh failed ({err})")
        body = r.json()
        token, expires_in = body["access_token"], int(body.get("expires_in", 3600))
        _cache[user_id] = (token, time.time() + expires_in)
        log.info("google access token refreshed", extra={"user_id": str(user_id), "expires_in": expires_in})
        return token


def forget_user(user_id: uuid.UUID) -> None:
    _cache.pop(user_id, None)
