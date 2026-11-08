import "server-only";

import { getToken } from "next-auth/jwt";
import { headers } from "next/headers";

import { API_URL, SECURE_COOKIES } from "@/lib/config";

/** The API session token from the encrypted Auth.js cookie (server-side only). */
export async function apiTokenFrom(req: Request | { headers: Headers }): Promise<string | undefined> {
  const token = await getToken({
    req,
    secret: process.env.AUTH_SECRET,
    secureCookie: SECURE_COOKIES,
  });
  return token?.apiToken;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(`API ${status}`);
  }
}

/** Call the API as the signed-in user from a server component / server action. */
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const apiToken = await apiTokenFrom({ headers: await headers() });
  if (!apiToken) throw new ApiError(401, "not signed in");
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { ...init.headers, authorization: `Bearer ${apiToken}` },
    cache: "no-store",
  });
  if (!res.ok) {
    throw new ApiError(res.status, await res.json().catch(() => res.statusText));
  }
  return (await res.json()) as T;
}

export type Me = {
  id: string;
  email: string;
  name: string | null;
  picture_url: string | null;
  short_id: string;
  org: { id: string; slug: string; display_name: string; is_personal: boolean };
  google_connected: boolean;
  bigquery_scope_granted: boolean;
  google_token_ok: boolean | null;
  settings: Record<string, unknown>;
};
