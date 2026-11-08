// Liveness probe for compose / Cloud Run. No backend calls.
export function GET() {
  return Response.json({ status: "ok" });
}
