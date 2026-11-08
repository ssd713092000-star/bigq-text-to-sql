"""Sign-in handshake, orgs, encrypted refresh tokens, sessions, Google token refresh."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import sign_in


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_gmail_user_gets_personal_org_and_encrypted_refresh_token(api):
    r = sign_in(api, "Alice@Gmail.com", refresh_token="1//super-secret-refresh-token")
    assert r.status_code == 201, r.text
    me = r.json()["user"]
    assert me["email"] == "alice@gmail.com"  # lowercased: also the Mem0 user_id later
    assert me["org"]["is_personal"] is True
    assert me["org"]["slug"] == f"user-{me['short_id']}"
    assert me["google_connected"] and me["bigquery_scope_granted"]

    # stored encrypted, decrypts back
    from sqlalchemy import select

    from app import db
    from app.models import UserCredential
    from app.security import decrypt

    async def stored():
        async with db._sessions() as s:
            return (await s.execute(select(UserCredential))).scalar_one().refresh_token_enc

    enc = api.portal.call(stored)  # run on the app's event loop
    assert b"super-secret" not in enc
    assert decrypt(enc) == "1//super-secret-refresh-token"


def test_workspace_users_share_org_but_email_suffix_alone_does_not(api):
    a = sign_in(api, "a@acme.com", hd="acme.com").json()["user"]
    b = sign_in(api, "b@acme.com", hd="acme.com").json()["user"]
    assert a["org"]["slug"] == b["org"]["slug"] == "acme"
    assert a["org"]["id"] == b["org"]["id"] and a["org"]["is_personal"] is False

    # consumer Google account using an @acme.com address: no `hd`, so NOT in the acme org
    c = sign_in(api, "intruder@acme.com").json()["user"]
    assert c["org"]["is_personal"] is True and c["org"]["id"] != a["org"]["id"]


def test_org_slug_collision_uses_full_domain(api):
    assert sign_in(api, "x@acme.com", hd="acme.com").json()["user"]["org"]["slug"] == "acme"
    assert sign_in(api, "y@acme.co.uk", hd="acme.co.uk").json()["user"]["org"]["slug"] == "acme-co-uk"


def test_me_requires_session_and_sign_out_revokes_it(api):
    assert api.get("/me").status_code == 401
    assert api.get("/me", headers=bearer("not-a-real-token")).status_code == 401

    token = sign_in(api, "bob@gmail.com").json()["session_token"]
    r = api.get("/me", headers=bearer(token))
    assert r.status_code == 200 and r.json()["email"] == "bob@gmail.com"

    assert api.delete("/auth/session", headers=bearer(token)).status_code == 204
    assert api.get("/me", headers=bearer(token)).status_code == 401


def test_bad_or_unverified_google_tokens_are_rejected(api):
    r = api.post("/auth/session", json={"id_token": "eyJhbGciOi.not-a-google-token.sig"})
    assert r.status_code == 401 and r.json()["detail"] == "invalid_google_token"
    assert sign_in(api, "eve@gmail.com", verified=False).status_code == 401


def test_relogin_reuses_user_and_keeps_refresh_token_when_none_sent(api):
    first = sign_in(api, "carol@gmail.com").json()
    again = sign_in(api, "carol@gmail.com", refresh_token=None).json()
    assert again["user"]["id"] == first["user"]["id"]
    assert again["user"]["google_connected"] is True       # old refresh token kept
    assert again["session_token"] != first["session_token"]  # one session per sign-in
    # both sessions are valid until signed out
    assert api.get("/me", headers=bearer(first["session_token"])).status_code == 200


def test_bigquery_scope_unticked_is_reported(api):
    me = sign_in(api, "dan@gmail.com", scope="openid email profile").json()["user"]
    assert me["google_connected"] is True and me["bigquery_scope_granted"] is False


def test_expired_session_is_rejected(api):
    token = sign_in(api, "old@gmail.com").json()["session_token"]
    from sqlalchemy import update

    from app import db
    from app.models import Session

    async def expire():
        async with db._sessions() as s:
            await s.execute(update(Session).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
            await s.commit()

    api.portal.call(expire)
    r = api.get("/me", headers=bearer(token))
    assert r.status_code == 401 and r.json()["detail"] == "session expired"


def test_check_google_mints_access_token(api, monkeypatch):
    from app import google_oauth

    calls = []

    async def fake_access_token_for(user_id, refresh_token, **_):
        calls.append(refresh_token)
        return "ya29.fake"

    monkeypatch.setattr(google_oauth, "access_token_for", fake_access_token_for)
    token = sign_in(api, "erin@gmail.com", refresh_token="1//erin-refresh").json()["session_token"]
    me = api.get("/me?check_google=true", headers=bearer(token)).json()
    assert me["google_token_ok"] is True
    assert calls == ["1//erin-refresh"]  # decrypted from the DB


# ---------------------------------------------------------------- token refresh (no DB)
async def test_access_token_refresh_is_cached_and_invalid_grant_surfaces(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
    from app import google_oauth
    from app.config import get_settings

    get_settings.cache_clear()
    google_oauth._cache.clear()
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = dict(x.split("=") for x in request.content.decode().split("&"))
        hits.append(body)
        if body["refresh_token"] == "revoked":
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"access_token": "ya29.new", "expires_in": 3599})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    uid = uuid.uuid4()
    assert await google_oauth.access_token_for(uid, "good", client=client) == "ya29.new"
    assert await google_oauth.access_token_for(uid, "good", client=client) == "ya29.new"
    assert len(hits) == 1  # second call served from cache
    assert hits[0]["grant_type"] == "refresh_token" and hits[0]["client_id"] == "cid"

    with pytest.raises(google_oauth.GoogleAuthError, match="invalid_grant"):
        await google_oauth.access_token_for(uuid.uuid4(), "revoked", client=client)
    await client.aclose()
    get_settings.cache_clear()
