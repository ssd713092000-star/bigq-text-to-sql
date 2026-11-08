"""Request authentication: `Authorization: Bearer <session token>` -> current user.

The web app's server keeps the session token inside its encrypted Auth.js cookie and adds it to every
API call it forwards; the browser never sees it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import google_oauth
from app.db import get_session
from app.models import Session, User, UserCredential
from app.security import decrypt, hash_token

log = logging.getLogger("api.auth")
_TOUCH_EVERY = timedelta(minutes=5)

DB = Annotated[AsyncSession, Depends(get_session)]


@dataclass
class AuthContext:
    user: User
    session: Session


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})


async def current_auth(db: DB, authorization: Annotated[str | None, Header()] = None) -> AuthContext:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized("missing bearer token")
    sess = (await db.execute(select(Session).where(Session.token_hash == hash_token(token)))).scalar_one_or_none()
    now = datetime.now(UTC)
    if sess is None:
        raise _unauthorized("invalid session")
    if sess.expires_at <= now:
        await db.delete(sess)
        await db.commit()
        raise _unauthorized("session expired")
    if sess.last_seen_at is None or now - sess.last_seen_at > _TOUCH_EVERY:
        sess.last_seen_at = now
        await db.commit()
    return AuthContext(user=sess.user, session=sess)


CurrentAuth = Annotated[AuthContext, Depends(current_auth)]


async def current_user(auth: CurrentAuth) -> User:
    return auth.user


CurrentUser = Annotated[User, Depends(current_user)]


async def google_access_token(db: AsyncSession, user: User) -> str:
    """Fresh Google access token for `user` (to call BigQuery / the MCP server as them)."""
    cred = await db.get(UserCredential, user.id)
    if cred is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="google_not_connected")
    try:
        return await google_oauth.access_token_for(user.id, decrypt(cred.refresh_token_enc))
    except google_oauth.GoogleAuthError as e:
        # invalid_grant: the user revoked access or the token expired -> they must sign in again
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="google_reauth_required" if "invalid_grant" in str(e) else str(e)) from e
