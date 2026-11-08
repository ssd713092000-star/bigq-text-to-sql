"""Sign-in handshake with the web app, sign-out, and the current user."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app import google_oauth
from app.auth import DB, CurrentAuth, CurrentUser, google_access_token
from app.config import get_settings
from app.models import Session, User, UserCredential
from app.orgs import org_for_domain, personal_org
from app.security import encrypt, new_session_token, short_id

log = logging.getLogger("api.auth")
router = APIRouter(tags=["auth"])


# ---------------------------------------------------------------- schemas
class SessionIn(BaseModel):
    """Sent by the web app's server right after Google sign-in (never by the browser)."""

    id_token: str = Field(min_length=20)
    refresh_token: str | None = None  # Google sends one on every consent (we always ask with prompt=consent)
    scope: str | None = None          # space-separated scopes the user actually granted


class OrgOut(BaseModel):
    id: uuid.UUID
    slug: str
    display_name: str
    is_personal: bool


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    picture_url: str | None
    short_id: str
    org: OrgOut
    google_connected: bool        # a refresh token is stored
    bigquery_scope_granted: bool  # the user ticked the BigQuery permission on the consent screen
    google_token_ok: bool | None = None  # only with ?check_google=true: a fresh access token could be minted
    settings: dict


class SessionOut(BaseModel):
    session_token: str
    expires_at: datetime
    user: MeOut


async def _me(db, user: User, check_google: bool = False) -> MeOut:
    cred = await db.get(UserCredential, user.id)
    scopes = set((cred.scopes if cred else "").split())
    out = MeOut(
        id=user.id, email=user.email, name=user.name, picture_url=user.picture_url, short_id=user.short_id,
        org=OrgOut(id=user.org.id, slug=user.org.slug, display_name=user.org.display_name,
                   is_personal=user.org.is_personal),
        google_connected=cred is not None, bigquery_scope_granted=google_oauth.BQ_SCOPE in scopes,
        settings=user.settings or {},
    )
    if check_google and cred is not None:
        try:
            await google_access_token(db, user)
            out.google_token_ok = True
        except HTTPException:
            out.google_token_ok = False
    return out


# ---------------------------------------------------------------- routes
@router.post("/auth/session", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionIn, request: Request, db: DB) -> SessionOut:
    try:
        ident = await google_oauth.verify_id_token(body.id_token)
    except google_oauth.GoogleAuthError as e:
        log.warning("sign-in rejected", extra={"reason": str(e)})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_google_token") from e

    now = datetime.now(UTC)
    user = (await db.execute(
        select(User).where(or_(User.google_sub == ident.sub, User.email == ident.email))
    )).unique().scalar_one_or_none()

    if user is None:
        sid = short_id()
        while (await db.execute(select(User.id).where(User.short_id == sid))).first():
            sid = short_id()
        org = (await org_for_domain(db, ident.hosted_domain) if ident.hosted_domain
               else await personal_org(db, sid, ident.name or ident.email.split("@")[0]))
        user = User(email=ident.email, short_id=sid, google_sub=ident.sub, org_id=org.id, org=org)
        db.add(user)
        created = True
    else:
        created = False
    user.google_sub = ident.sub
    user.email = ident.email
    user.name = ident.name
    user.picture_url = ident.picture
    user.last_login_at = now
    await db.flush()

    if body.refresh_token:
        cred = await db.get(UserCredential, user.id)
        enc = encrypt(body.refresh_token)
        if cred is None:
            db.add(UserCredential(user_id=user.id, refresh_token_enc=enc, scopes=body.scope or ""))
        else:
            cred.refresh_token_enc, cred.scopes = enc, body.scope or cred.scopes
        google_oauth.forget_user(user.id)  # drop any access token minted from the old refresh token

    token, token_hash = new_session_token()
    expires = now + timedelta(hours=get_settings().session_ttl_hours)
    db.add(Session(user_id=user.id, token_hash=token_hash, expires_at=expires,
                   user_agent=(request.headers.get("x-client-user-agent") or "")[:400] or None))
    await db.commit()  # expire_on_commit=False: user/org stay loaded

    log.info("sign-in", extra={"user": user.email, "org": user.org.slug, "new_user": created,
                               "workspace_domain": ident.hosted_domain, "refresh_token": bool(body.refresh_token)})
    return SessionOut(session_token=token, expires_at=expires, user=await _me(db, user))


@router.delete("/auth/session", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(auth: CurrentAuth, db: DB) -> None:
    await db.delete(auth.session)  # same AsyncSession: FastAPI caches the DB dependency per request
    await db.commit()
    log.info("sign-out", extra={"user": auth.user.email})


@router.get("/me", response_model=MeOut)
async def me(user: CurrentUser, db: DB,
             check_google: bool = Query(False, description="also mint a Google access token to prove it works")
             ) -> MeOut:
    return await _me(db, user, check_google=check_google)
