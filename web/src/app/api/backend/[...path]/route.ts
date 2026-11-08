// Backend-for-frontend: the browser calls /api/backend/<path>; this forwards to the API as the signed-in
// user, adding the API session token from the encrypted Auth.js cookie. Bodies and responses are streamed
// through unchanged (so Server-Sent Events from the chat endpoint work too).
import type { NextRequest } from "next/server";

import { apiTokenFrom } from "@/lib/api";
import { API_URL } from "@/lib/config";

const FORWARD_REQUEST = ["content-type", "accept", "last-event-id"];
const FORWARD_RESPONSE = ["content-type", "cache-control", "content-disposition", "x-request-id"];

async function proxy(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  if (path[0] === "auth") {
    // session create/delete only happen through Auth.js on the server
    return Response.json({ detail: "not found" }, { status: 404 });
  }
  const apiToken = await apiTokenFrom(req);
  if (!apiToken) {
    return Response.json({ detail: "not signed in" }, { status: 401 });
  }

  const url = new URL(`${API_URL}/${path.map(encodeURIComponent).join("/")}`);
  url.search = req.nextUrl.search;

  const headers = new Headers({ authorization: `Bearer ${apiToken}` });
  for (const h of FORWARD_REQUEST) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  headers.set("x-client-user-agent", req.headers.get("user-agent") ?? "");

  const hasBody = !["GET", "HEAD"].includes(req.method);
  const res = await fetch(url, {
    method: req.method,
    headers,
    body: hasBody ? req.body : undefined,
    // @ts-expect-error -- Node fetch needs duplex for streamed request bodies
    duplex: hasBody ? "half" : undefined,
    cache: "no-store",
    signal: req.signal, // browser disconnects -> stop the upstream request
    redirect: "manual",
  });

  const out = new Headers();
  for (const h of FORWARD_RESPONSE) {
    const v = res.headers.get(h);
    if (v) out.set(h, v);
  }
  return new Response(res.body, { status: res.status, headers: out });
}

export { proxy as DELETE, proxy as GET, proxy as PATCH, proxy as POST, proxy as PUT };
