// Auth.js (next-auth v5): Google sign-in. Reads AUTH_SECRET, AUTH_GOOGLE_ID, AUTH_GOOGLE_SECRET, AUTH_URL.
//
// Right after Google sign-in, the server side of this app hands the Google tokens to the API (POST /auth/session).
// The API verifies the ID token, stores the refresh token encrypted, and returns an API session token.
// That token lives only inside Auth.js's encrypted, httpOnly session cookie; it's never put in the
// `session` object (which /api/auth/session exposes to browser JavaScript).
import NextAuth from "next-auth";
import Google from "next-auth/providers/google";

import { API_URL } from "@/lib/config";

export const SESSION_MAX_AGE_S = 7 * 24 * 60 * 60; // keep equal to the API's SESSION_TTL_HOURS
const BIGQUERY_SCOPE = "https://www.googleapis.com/auth/bigquery";

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    Google({
      authorization: {
        params: {
          scope: `openid email profile ${BIGQUERY_SCOPE}`,
          access_type: "offline", // ask for a refresh token...
          prompt: "consent", // ...every time, so the API always has a current one
        },
      },
    }),
  ],
  session: { strategy: "jwt", maxAge: SESSION_MAX_AGE_S },
  callbacks: {
    async jwt({ token, account }) {
      if (account) {
        // first call after Google sign-in: create the API session
        const res = await fetch(`${API_URL}/auth/session`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            id_token: account.id_token,
            refresh_token: account.refresh_token,
            scope: account.scope,
          }),
          cache: "no-store",
        });
        if (!res.ok) {
          throw new Error(`API sign-in failed (${res.status})`); // aborts sign-in -> Auth.js error page
        }
        const s = await res.json();
        token.apiToken = s.session_token;
        token.apiTokenExpires = Date.parse(s.expires_at);
        token.userId = s.user.id;
        token.orgSlug = s.user.org.slug;
        return token;
      }
      if (token.apiTokenExpires && Date.now() >= token.apiTokenExpires) {
        return null; // API session expired: end the web session too
      }
      return token;
    },
    async session({ session, token }) {
      session.user.id = token.userId ?? "";
      session.user.orgSlug = token.orgSlug ?? "";
      return session; // deliberately without token.apiToken
    },
  },
  events: {
    async signOut(message) {
      // revoke the API session as well (jwt strategy: the decoded token is passed in)
      const apiToken = "token" in message ? message.token?.apiToken : undefined;
      if (apiToken) {
        await fetch(`${API_URL}/auth/session`, {
          method: "DELETE",
          headers: { authorization: `Bearer ${apiToken}` },
          cache: "no-store",
        }).catch(() => undefined);
      }
    },
  },
});
