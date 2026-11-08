# Sign-in and sessions

Google is the only sign-in method. BigQuery queries run **as the signed-in user**, so BigQuery IAM decides what
each person can see.

## Flow

```
browser ──(1) Sign in with Google──▶ web (Auth.js) ──▶ accounts.google.com
                                       │  scope: openid email profile bigquery
                                       │  access_type=offline, prompt=consent, PKCE
        ◀──────────── code ───────────┘
web server ──(2) POST /auth/session {id_token, refresh_token, scope}──▶ api
                                       api verifies the ID token (Google certs, aud = our client id,
                                       verified email), upserts user + org, stores the refresh token
                                       Fernet-encrypted, returns an opaque API session token
web stores the API token inside its encrypted, httpOnly Auth.js cookie (never in browser-visible JSON)

browser ──(3) /api/backend/<path>──▶ web server ──Authorization: Bearer <api token>──▶ api
                                     (backend-for-frontend proxy; streams bodies and SSE through)

api ──(4) refresh token ──▶ oauth2.googleapis.com/token ──▶ access token (cached until 2 min before expiry)
       └─▶ used as the user's identity for BigQuery / the MCP server (phase 3)
```

Why a proxy instead of the browser calling the API directly: web and api are on different `*.run.app` hosts, and
`run.app` is on the Public Suffix List. Each service therefore counts as its own site, so an api cookie would be a
third-party cookie, which browsers block. With the proxy, no token ever reaches browser JavaScript and the API needs no CORS.

## Orgs

The org comes from Google's **`hd` (hosted domain) claim**, which Google only sets for Google Workspace accounts on a
verified domain:

| Account | Org |
|---|---|
| Workspace user `a@acme.com` (`hd=acme.com`) | `acme`, shared with every acme.com Workspace user |
| `someone@gmail.com` | personal org `user-<shortid>` |
| Consumer Google account using an `@acme.com` address (no `hd`) | personal org, **not** `acme` |

The email suffix alone isn't trusted, because anyone can register a Google account with any email address. Slug
collisions use the whole domain (`acme.co.uk` becomes `acme-co-uk`). The display name can be renamed; the slug never changes.

## Sessions

- API sessions are random 256-bit tokens. Only their SHA-256 is stored (`app.sessions`). They last 7 days, the same
  as the Auth.js session.
- Sign-out (Auth.js `signOut`) deletes the API session row, so the token stops working immediately.
- Each sign-in sends a fresh refresh token (`prompt=consent`). If the user revoked access at
  https://myaccount.google.com/permissions, the next refresh fails with `invalid_grant`, and the API answers
  `403 google_reauth_required`.
- If the user unticks BigQuery on Google's consent screen, `/me` shows `bigquery_scope_granted: false` and the
  home page tells them to sign in again.

## Keys

| Secret | Used by | Local (`infra/.env`) | Cloud Run (Secret Manager) |
|---|---|---|---|
| OAuth client secret | web (Auth.js), api (refresh) | `GOOGLE_CLIENT_SECRET` | `google-oauth-client-secret` |
| Auth.js cookie key | web | `AUTH_SECRET` | `auth-secret` |
| Refresh-token encryption | api | `TOKEN_ENCRYPTION_KEY` | `token-encryption-key` |

To rotate `TOKEN_ENCRYPTION_KEY`, set it to `new_key,old_key` (the first key encrypts, all of them decrypt), deploy,
and remove the old key once every user has signed in again.

## OAuth client setup (APIs & Services > Credentials > your web client)

| | Local | Cloud Run |
|---|---|---|
| Authorized JavaScript origin | `http://localhost:3000` | `https://analyst-web-<project-number>.asia-south1.run.app` |
| Authorized redirect URI | `http://localhost:3000/api/auth/callback/google` | `https://analyst-web-<project-number>.asia-south1.run.app/api/auth/callback/google` |

The BigQuery scope is "sensitive". While the consent screen is in **Testing** mode, only listed test users can
sign in. Publishing to production for external users requires Google's verification.
