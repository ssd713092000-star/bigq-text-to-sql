"""
Read-only BigQuery MCP server with Google OAuth (FastMCP), in one file.

With OAuth on, each user signs in with Google and queries run with THEIR Google
credentials, so BigQuery IAM decides what they can see. With AUTH_MODE=none it
uses your local gcloud credentials (ADC), which is handy for development.

Every run_sql call shows the SQL + estimated GB/cost to the user and waits for approval.
Results bigger than BQ_MAX_ROWS are exported to GCS and returned as a download link.

Verified examples (search_examples / save_example) need rag_store.py in the same folder.

Install:
    uv add fastmcp google-cloud-bigquery google-cloud-storage google-auth google-cloud-aiplatform

Run:
    uv run bq_mcp_server.py            # HTTP on http://127.0.0.1:8000/mcp
    uv run bq_mcp_server.py --stdio    # stdio (local only, no OAuth)

Environment (.env next to this file, or real env vars):
    BQ_PROJECT=your-gcp-project-id        # project that runs (and pays for) the query jobs
    BQ_DATASET=nyc_taxi
    BQ_LOCATION=asia-south1
    BQ_EXTRA_DATASETS=                    # optional "project.dataset,project.dataset"
    BQ_MAX_GB=2                           # per-query scan limit
    BQ_MAX_ROWS=500                       # rows returned inline; bigger results go to GCS

    REQUIRE_APPROVAL=true                 # show SQL and wait for the user's OK before running
    APPROVAL_TTL_S=600                    # how long an approval_id stays valid (fallback flow)

    EXPORT_BUCKET=your-bucket-name        # GCS bucket for large results (same location as the dataset)
    EXPORT_PREFIX=bq-mcp-exports
    EXPORT_FORMAT=CSV                     # CSV | PARQUET
    SIGNER_SERVICE_ACCOUNT=               # optional SA email -> time-limited signed URLs
    URL_EXPIRY_MIN=60                     # signed URL lifetime

    AUTH_MODE=google                      # google | none
    GOOGLE_CLIENT_ID=xxxx.apps.googleusercontent.com
    GOOGLE_CLIENT_SECRET=xxxx
    MCP_BASE_URL=http://localhost:8000    # public URL of this server
    JWT_SIGNING_KEY=some-long-random-string   # keeps sessions valid across restarts
    ALLOWED_EMAILS=                       # optional "a@x.com,b@y.com"; empty = any Google user
    ALLOWED_DOMAINS=                      # optional "iserveu.in,example.com"
    MCP_HOST=127.0.0.1
    MCP_PORT=8000

    LOG_LEVEL=INFO                        # DEBUG adds full results, sample rows and tracebacks
    LOG_FILE=                             # optional, e.g. bq_mcp.log (rotating 10 MB x 5)
    LOG_MAX_CHARS=2000                    # truncate long values (SQL, results) in log lines
"""

from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import functools
import hashlib
import hmac
import inspect
import logging
import os
import re
import secrets
import sys
import time
from contextvars import ContextVar
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import quote

import mcp_types
from fastmcp import Context, FastMCP
from google.cloud import bigquery

log = logging.getLogger("bq_mcp")


# ═════════════════════════════ config ═════════════════════════════
def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(Path(__file__).with_name(".env"))


def _csv(name: str) -> list[str]:
    return [x.strip().lower() for x in os.getenv(name, "").split(",") if x.strip()]


PROJECT = os.getenv("BQ_PROJECT", "")
DATASET = os.getenv("BQ_DATASET", "nyc_taxi")
LOCATION = os.getenv("BQ_LOCATION", "US")
ALLOWED_DATASETS = {f"{PROJECT}.{DATASET}".lower(), *_csv("BQ_EXTRA_DATASETS")}
MAX_BYTES = int(float(os.getenv("BQ_MAX_GB", "2")) * 1024**3)
MAX_ROWS = int(os.getenv("BQ_MAX_ROWS", "500"))
TIMEOUT_S = float(os.getenv("BQ_TIMEOUT_S", "60"))

REQUIRE_APPROVAL = os.getenv("REQUIRE_APPROVAL", "true").lower() in ("1", "true", "yes")
APPROVAL_TTL_S = int(os.getenv("APPROVAL_TTL_S", "600"))
APPROVAL_SECRET = (os.getenv("JWT_SIGNING_KEY") or secrets.token_hex(32)).encode()

EXPORT_BUCKET = os.getenv("EXPORT_BUCKET", "")
EXPORT_PREFIX = os.getenv("EXPORT_PREFIX", "bq-mcp-exports").strip("/")
EXPORT_FORMAT = os.getenv("EXPORT_FORMAT", "CSV").upper()
SIGNER_SA = os.getenv("SIGNER_SERVICE_ACCOUNT", "")
URL_EXPIRY_MIN = int(os.getenv("URL_EXPIRY_MIN", "60"))

AUTH_MODE = os.getenv("AUTH_MODE", "google").lower()
ALLOWED_EMAILS = set(_csv("ALLOWED_EMAILS"))
ALLOWED_DOMAINS = set(_csv("ALLOWED_DOMAINS"))
HOST = os.getenv("MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_PORT") or os.getenv("PORT") or "8000")  # Cloud Run sets PORT

BQ_SCOPE = "https://www.googleapis.com/auth/bigquery"
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,299}$")
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()      # DEBUG shows rows/args in full detail
LOG_FILE = os.getenv("LOG_FILE", "")                     # optional path; rotating, 10 MB x 5
LOG_MAX_CHARS = int(os.getenv("LOG_MAX_CHARS", "2000"))  # truncate long values (SQL, results) in logs


# ═════════════════════════════ logging ═════════════════════════════
# Every log line carries the tool call id and user, so one request can be followed end to end
# (contextvars also flow into asyncio.to_thread workers).
_call_id: ContextVar[str] = ContextVar("call_id", default="-")
_call_user: ContextVar[str] = ContextVar("call_user", default="-")


class _CallContext(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.call_id = _call_id.get()
        record.user = _call_user.get()
        return True


def setup_logging() -> None:
    """Configure the 'bq_mcp' logger tree once. Logs go to stderr (stdout is the stdio channel)."""
    root = logging.getLogger("bq_mcp")
    if getattr(root, "_bq_configured", False):
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s [call=%(call_id)s user=%(user)s] %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if LOG_FILE:
        handlers.append(RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024**2, backupCount=5, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(_CallContext())
        root.addHandler(h)
    root.setLevel(LOG_LEVEL)
    root.propagate = False
    root._bq_configured = True  # type: ignore[attr-defined]


setup_logging()


def short(v: Any, limit: int | None = None) -> str:
    """Compact, truncated text for logs (SQL is collapsed onto one line)."""
    limit = limit or LOG_MAX_CHARS
    s = " ".join(v.split()) if isinstance(v, str) else repr(v)
    return s if len(s) <= limit else f"{s[:limit]}...(+{len(s) - limit} chars)"


_RESULT_KEYS = ("ok", "error", "hint", "cancelled", "needs_approval", "needs_confirmation", "delivery",
                "total_rows", "row_count", "count", "gb_processed", "cost_usd", "estimated_gb",
                "estimated_cost_usd", "job_id", "saved", "gcs_uri", "export_error", "seconds")


def _summarize(result: Any) -> str:
    if isinstance(result, dict):
        return ", ".join(f"{k}={short(result[k], 300)}" for k in _RESULT_KEYS if k in result) or "{}"
    return type(result).__name__


def logged_tool(fn):
    """Log START (arguments), END (outcome + duration) and crashes for an MCP tool, sync or async."""
    name = fn.__name__
    sig = inspect.signature(fn)

    def start(args, kwargs):
        tokens = (_call_id.set(secrets.token_hex(4)), _call_user.set("-"))
        try:
            bound = sig.bind_partial(*args, **kwargs).arguments
        except TypeError:
            bound = kwargs
        shown = ", ".join(f"{k}={short(v)}" for k, v in bound.items() if not isinstance(v, Context))
        log.info("tool=%s START %s", name, shown or "(no args)")
        return tokens, time.perf_counter()

    def finish(tokens, t0, result=None, exc: BaseException | None = None):
        ms = round((time.perf_counter() - t0) * 1000)
        try:
            if exc is not None:
                log.error("tool=%s CRASH after %sms: %s: %s", name, ms, type(exc).__name__, exc, exc_info=exc)
            elif isinstance(result, dict) and result.get("ok") is False:
                log.warning("tool=%s FAIL in %sms: %s", name, ms, _summarize(result))
            elif not isinstance(result, dict):
                log.info("tool=%s PENDING in %sms: returned %s (waiting for user input)", name, ms,
                         type(result).__name__)
            else:
                log.info("tool=%s OK in %sms: %s", name, ms, _summarize(result))
            if result is not None:
                log.debug("tool=%s full result: %s", name, short(result))
        finally:
            _call_id.reset(tokens[0])
            _call_user.reset(tokens[1])

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            tokens, t0 = start(args, kwargs)
            try:
                result = await fn(*args, **kwargs)
            except BaseException as e:
                finish(tokens, t0, exc=e)
                raise
            finish(tokens, t0, result)
            return result
        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args, **kwargs):
        tokens, t0 = start(args, kwargs)
        try:
            result = fn(*args, **kwargs)
        except BaseException as e:
            finish(tokens, t0, exc=e)
            raise
        finish(tokens, t0, result)
        return result
    return sync_wrapper


# ═════════════════════════════ auth ═════════════════════════════
def build_auth():
    """Google OAuth via FastMCP's OAuth proxy. MCP clients get the login flow automatically."""
    if AUTH_MODE == "none":
        log.warning("AUTH_MODE=none: no login, queries use this machine's gcloud credentials")
        return None

    from fastmcp.server.auth.providers.google import GoogleProvider

    for var in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        if not os.getenv(var):
            raise RuntimeError(f"{var} is required when AUTH_MODE=google")

    return GoogleProvider(
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        base_url=os.getenv("MCP_BASE_URL", f"http://localhost:{PORT}"),
        # bigquery scope lets us run queries AS the signed-in user
        required_scopes=["openid", "https://www.googleapis.com/auth/userinfo.email", BQ_SCOPE],
        jwt_signing_key=os.getenv("JWT_SIGNING_KEY"),
    )


class AuthError(Exception):
    pass


def current_user() -> tuple[str, Any]:
    """(email, credentials) for this request. In AUTH_MODE=none: ('local', None) -> ADC."""
    if AUTH_MODE == "none":
        _call_user.set("local")
        log.debug("auth: AUTH_MODE=none, using local ADC credentials")
        return "local", None

    from fastmcp.server.dependencies import get_access_token
    from google.oauth2.credentials import Credentials

    token = get_access_token()
    if token is None:
        log.warning("auth: request has no access token (not signed in)")
        raise AuthError("Not signed in.")

    email = (token.claims or {}).get("email", "").lower()
    _call_user.set(email or "unknown")
    if ALLOWED_EMAILS or ALLOWED_DOMAINS:
        domain = email.split("@")[-1] if "@" in email else ""
        if email not in ALLOWED_EMAILS and domain not in ALLOWED_DOMAINS:
            log.warning("auth: DENIED email=%r domain=%r (not in ALLOWED_EMAILS/ALLOWED_DOMAINS)", email, domain)
            raise AuthError(f"{email or 'This account'} is not allowed to use this server.")

    log.debug("auth: signed in as %s (client_id=%s, scopes=%s)", email,
              getattr(token, "client_id", "?"), getattr(token, "scopes", "?"))
    # token.token is the user's upstream Google access token (FastMCP swaps it in)
    return email, Credentials(token=token.token)


def bq_client() -> tuple[str, bigquery.Client]:
    email, creds = current_user()
    # Jobs run in PROJECT; with user creds, BigQuery checks that user's IAM roles
    log.debug("bigquery client: project=%s location=%s creds=%s", PROJECT, LOCATION,
              "user-oauth" if creds else "ADC")
    return email, bigquery.Client(project=PROJECT, location=LOCATION, credentials=creds)


# ═════════════════════════════ approval (human in the loop) ═════════════════════════════
def _sign(payload: str) -> str:
    return hmac.new(APPROVAL_SECRET, payload.encode(), hashlib.sha256).hexdigest()[:32]


def make_approval_id(sql: str, email: str) -> str:
    """Stateless token bound to this exact SQL + user + expiry."""
    exp = int(time.time()) + APPROVAL_TTL_S
    return f"{exp}.{_sign(f'{exp}|{email}|{sql.strip()}')}"


def approval_valid(approval_id: str | None, sql: str, email: str) -> bool:
    if not approval_id:
        return False
    try:
        exp_s, sig = approval_id.split(".", 1)
        expired = int(exp_s) < time.time()
        matches = hmac.compare_digest(sig, _sign(f"{exp_s}|{email}|{sql.strip()}"))
    except ValueError:
        log.warning("approval: malformed approval_id %r", short(approval_id, 80))
        return False
    if expired:
        log.warning("approval: approval_id expired at %s", dt.datetime.fromtimestamp(int(exp_s)).isoformat())
    elif not matches:
        log.warning("approval: approval_id does not match this SQL/user (content changed or wrong user)")
    else:
        log.info("approval: valid approval_id (expires in %ss)", int(int(exp_s) - time.time()))
    return matches and not expired


APPROVE_KEY = "approve_query"
APPROVE_CHOICES = ["Run query", "Cancel"]


def _approval_message(sql: str, info: DryRun) -> str:
    return (
        "The agent wants to run this BigQuery query:\n\n"
        f"{sql.strip()}\n\n"
        f"Estimated scan: {gb(info.bytes)} GB (~${cost_usd(info.bytes)})\n"
        f"Tables: {', '.join(info.tables) or '-'}"
    )


def client_can_elicit(ctx: Context) -> bool:
    """Did the MCP client declare it can show forms to the user?"""
    try:
        caps = ctx.session.client_params.capabilities
        can = bool(caps and caps.elicitation)
    except Exception as e:
        log.debug("approval: could not read client capabilities (%s)", e)
        return False
    log.debug("approval: client supports elicitation=%s", can)
    return can


async def confirm(ctx: Context, *, key: str, message: str, title: str, choices: list[str],
                  bind: str, email: str):
    """Ask the user a yes/no question, on both MCP protocol eras. choices[0] means yes.

    `bind` is the exact content being approved (e.g. the SQL); the answer only counts for it.
    Returns 'approved' | 'rejected' | 'unsupported', or an InputRequiredResult that the
    tool must return as-is (2026-07-28 clients answer it and retry the call automatically).
    """
    # --- 2026-07-28 era: guard / multi-round-trip pattern (SEP-2322) ---
    responses = ctx.input_responses
    if responses and key in responses:
        # request_state is sealed by FastMCP; it ties the answer to the content we asked about
        if not approval_valid(ctx.request_state, bind, email):
            log.warning("approval[%s]: input response present but request_state invalid -> rejected", key)
            return "rejected"
        r = responses[key]
        content = getattr(r, "content", None) or {}
        action = getattr(r, "action", None)
        ok = action == "accept" and content.get("decision") == choices[0]
        log.info("approval[%s]: input-response action=%s decision=%r -> %s", key, action,
                 content.get("decision"), "approved" if ok else "rejected")
        return "approved" if ok else "rejected"

    if not client_can_elicit(ctx):
        log.info("approval[%s]: client cannot elicit -> approval_id fallback", key)
        return "unsupported"  # caller falls back to the approval_id flow

    # --- handshake era: imperative elicitation ---
    try:
        log.info("approval[%s]: asking user via elicitation (%r)", key, title)
        result = await ctx.elicit(message, response_type=choices, response_title=title)
        ok = result.action == "accept" and getattr(result, "data", None) == choices[0]
        log.info("approval[%s]: elicitation action=%s data=%r -> %s", key, result.action,
                 getattr(result, "data", None), "approved" if ok else "rejected")
        return "approved" if ok else "rejected"
    except Exception as e:
        if "2026-07-28" not in str(e):
            log.info("approval[%s]: elicitation failed (%s: %s); using approval_id flow", key, type(e).__name__, e)
            return "unsupported"
        log.info("approval[%s]: 2026-07-28 client, returning InputRequiredResult", key)

    # modern connection: ask via InputRequiredResult; the client retries the same call with the answer
    return mcp_types.InputRequiredResult(
        input_requests={key: mcp_types.ElicitRequest(params=mcp_types.ElicitRequestFormParams(
            message=message,
            requested_schema={
                "type": "object",
                "properties": {"decision": {"type": "string", "title": title, "enum": choices}},
                "required": ["decision"],
            },
        ))},
        request_state=make_approval_id(bind, email),
    )


async def ask_user_to_approve(ctx: Context, sql: str, info: DryRun, email: str):
    return await confirm(ctx, key=APPROVE_KEY, message=_approval_message(sql, info),
                         title="Run this query?", choices=APPROVE_CHOICES, bind=sql, email=email)


# ═════════════════════════════ large results -> GCS ═════════════════════════════
def _signed_url(blob) -> str | None:
    """V4 signed URL by impersonating SIGNER_SA (server's ADC needs Token Creator on it)."""
    if not SIGNER_SA:
        return None
    log.debug("export: signing URL for %s as %s (expires in %s min)", blob.name, SIGNER_SA, URL_EXPIRY_MIN)
    import google.auth
    from google.auth import impersonated_credentials

    source, _ = google.auth.default()
    signer = impersonated_credentials.Credentials(
        source_credentials=source, target_principal=SIGNER_SA,
        target_scopes=["https://www.googleapis.com/auth/devstorage.read_only"], lifetime=300,
    )
    return blob.generate_signed_url(version="v4", method="GET", credentials=signer,
                                    expiration=dt.timedelta(minutes=URL_EXPIRY_MIN))


def export_to_gcs(client: bigquery.Client, job: bigquery.QueryJob, creds: Any) -> dict[str, Any]:
    """Extract the query's result table to GCS and return download links."""
    from google.cloud import storage

    ext = {"CSV": "csv", "PARQUET": "parquet"}.get(EXPORT_FORMAT, "csv")
    folder = f"{EXPORT_PREFIX}/{dt.date.today():%Y/%m/%d}/{job.job_id}"
    cfg = bigquery.ExtractJobConfig(
        destination_format=bigquery.DestinationFormat.PARQUET if ext == "parquet" else bigquery.DestinationFormat.CSV,
        **({"print_header": True} if ext == "csv" else {}),
    )
    # Every query result lands in a temporary table (job.destination); export that, no re-run needed.
    # Wildcard lets BigQuery split results >1 GB into several files.
    dest = f"gs://{EXPORT_BUCKET}/{folder}/part-*.{ext}"
    log.info("export: extracting %s -> %s (format=%s)", job.destination, dest, ext)
    t0 = time.perf_counter()
    extract = client.extract_table(job.destination, dest, job_config=cfg, location=job.location)
    extract.result(timeout=TIMEOUT_S * 5)
    log.info("export: extract job %s done in %.2fs", extract.job_id, time.perf_counter() - t0)

    gcs = storage.Client(project=PROJECT)  # same user identity as the query
    files = []
    for blob in gcs.list_blobs(EXPORT_BUCKET, prefix=f"{folder}/"):
        files.append({
            "gcs_uri": f"gs://{EXPORT_BUCKET}/{blob.name}",
            "size_mb": round((blob.size or 0) / 1024**2, 2),
            # signed URL works for anyone holding it until expiry; otherwise a Google-login link
            "url": _signed_url(blob) or f"https://storage.cloud.google.com/{EXPORT_BUCKET}/{quote(blob.name)}",
        })
    log.info("export: %d file(s), %.2f MB total, url_type=%s", len(files),
             sum(f["size_mb"] for f in files), "signed" if SIGNER_SA else "authenticated")
    return {"format": ext, "folder": f"gs://{EXPORT_BUCKET}/{folder}/", "files": files,
            "url_type": "signed" if SIGNER_SA else "authenticated (Google login + bucket read access)",
            **({"expires_in_min": URL_EXPIRY_MIN} if SIGNER_SA else {})}


# ═════════════════════════════ guards & helpers ═════════════════════════════
@dataclass(frozen=True)
class DryRun:
    statement_type: str
    bytes: int
    tables: tuple[str, ...]


def gb(n: int) -> float:
    return round(n / 1024**3, 3)


def cost_usd(n: int, usd_per_tib: float = 6.25) -> float:
    """On-demand estimate; check your region's price (first 1 TiB/month is free)."""
    return round(n / 1024**4 * usd_per_tib, 4)


def err(msg: str, hint: str | None = None) -> dict[str, Any]:
    return {"ok": False, "error": msg, **({"hint": hint} if hint else {})}


def bq_msg(e: Exception) -> str:
    return getattr(e, "message", None) or str(e)


def tool_error(where: str, e: Exception, hint: str | None = None, msg: str | None = None) -> dict[str, Any]:
    """Log a caught exception (traceback when LOG_LEVEL=DEBUG) and turn it into an error dict."""
    log.warning("%s: %s: %s", where, type(e).__name__, short(bq_msg(e), 1000),
                exc_info=log.isEnabledFor(logging.DEBUG))
    return err(msg or bq_msg(e), hint)


def check_identifier(name: str, kind: str) -> str:
    if not IDENTIFIER.match(name or ""):
        raise ValueError(f"Invalid {kind}: {name!r}")
    return name


def resolve_table(table: str) -> str:
    """'table' | 'dataset.table' | 'project.dataset.table' -> fully qualified, allowed datasets only."""
    parts = table.strip().strip("`").split(".")
    if len(parts) == 1:
        parts = [PROJECT, DATASET, parts[0]]
    elif len(parts) == 2:
        parts = [PROJECT, *parts]
    if len(parts) != 3:
        raise ValueError(f"Bad table name: {table!r}")
    project, dataset, name = parts
    check_identifier(dataset, "dataset")
    check_identifier(name, "table")
    if f"{project}.{dataset}".lower() not in ALLOWED_DATASETS:
        raise ValueError(f"Dataset {project}.{dataset} is not allowed")
    return f"{project}.{dataset}.{name}"


def check_query(info: DryRun) -> dict[str, Any] | None:
    """None if OK, else an error dict. Read-only, in scope, within budget."""
    if info.statement_type.upper() != "SELECT":
        log.warning("guard: BLOCKED non-SELECT statement_type=%s", info.statement_type)
        return err(f"Only SELECT queries are allowed (got {info.statement_type}).",
                   "Rewrite as a single SELECT statement.")
    outside = sorted(t for t in info.tables if ".".join(t.lower().split(".")[:2]) not in ALLOWED_DATASETS)
    if outside:
        log.warning("guard: BLOCKED tables outside allowed datasets: %s (allowed=%s)", outside,
                    sorted(ALLOWED_DATASETS))
        return err(f"Query uses tables outside the allowed datasets: {outside}.",
                   "Call list_tables to see what you can query.")
    if info.bytes > MAX_BYTES:
        log.warning("guard: BLOCKED scan %s GB > limit %s GB", gb(info.bytes), gb(MAX_BYTES))
        return err(f"Query would scan {gb(info.bytes)} GB, over the {gb(MAX_BYTES)} GB limit.",
                   "Filter on the partition column (pickup_date) and select only needed columns.")
    log.debug("guard: passed (SELECT, in scope, %s GB <= %s GB)", gb(info.bytes), gb(MAX_BYTES))
    return None


def dry_run(client: bigquery.Client, sql: str) -> DryRun:
    log.info("dry-run: sql=%s", short(sql))
    t0 = time.perf_counter()
    job = client.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
    info = DryRun(
        statement_type=job.statement_type or "UNKNOWN",
        bytes=job.total_bytes_processed or 0,
        tables=tuple(f"{r.project}.{r.dataset_id}.{r.table_id}" for r in (job.referenced_tables or [])),
    )
    log.info("dry-run: type=%s est_gb=%s est_cost_usd=%s tables=%s (%.2fs)", info.statement_type,
             gb(info.bytes), cost_usd(info.bytes), list(info.tables), time.perf_counter() - t0)
    return info


def guarded(client: bigquery.Client, sql: str) -> tuple[DryRun | None, dict[str, Any] | None]:
    try:
        info = dry_run(client, sql)
    except Exception as e:
        log.warning("dry-run: FAILED %s: %s", type(e).__name__, short(bq_msg(e), 500))
        return None, err(bq_msg(e), "Fix the SQL and call validate_sql again.")
    problem = check_query(info)
    return (None, problem) if problem else (info, None)


def json_safe(v: Any) -> Any:
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, bytes):
        return v.hex()
    if isinstance(v, dict):
        return {k: json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [json_safe(x) for x in v]
    return v


def to_rows(it) -> list[dict[str, Any]]:
    return [{k: json_safe(v) for k, v in r.items()} for r in it]


def execute(client: bigquery.Client, sql: str, email: str, max_rows: int):
    cfg = bigquery.QueryJobConfig(
        maximum_bytes_billed=MAX_BYTES,  # BigQuery itself refuses anything bigger
        labels={"app": "bq-mcp", "user": re.sub(r"[^a-z0-9_-]", "_", email)[:63]},  # cost per user in billing
    )
    log.info("execute: submitting query (max_rows=%s, max_bytes_billed=%s GB, timeout=%ss)",
             max_rows, gb(MAX_BYTES), TIMEOUT_S)
    t0 = time.perf_counter()
    job = client.query(sql, job_config=cfg)
    log.info("execute: job_id=%s location=%s submitted", job.job_id, job.location)
    it = job.result(max_results=max_rows, timeout=TIMEOUT_S)
    rows = to_rows(it)
    log.info("execute: job_id=%s done in %.2fs total_rows=%s fetched=%s gb_processed=%s gb_billed=%s "
             "cache_hit=%s slot_ms=%s", job.job_id, time.perf_counter() - t0, it.total_rows, len(rows),
             gb(job.total_bytes_processed or 0), gb(job.total_bytes_billed or 0), job.cache_hit,
             job.slot_millis)
    log.debug("execute: first rows: %s", short(rows[:5]))
    return job, it, rows


# ═════════════════════════════ server & tools ═════════════════════════════
mcp = FastMCP(
    "bigquery",
    auth=build_auth(),
    instructions=(
        "Read-only access to a BigQuery dataset. Workflow: search_examples (reuse verified SQL) -> "
        "list_tables -> describe_table -> column_values / sample_rows when unsure of values -> "
        "validate_sql -> run_sql. After answering, ask the user if the answer is correct; "
        "only if they say yes, call save_example with their question and the SQL. "
        "Always filter large tables on their partition column. "
        "Use fully qualified names: `project.dataset.table`."
    ),
)


@mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
async def healthz(_request):
    """Liveness probe for compose / Cloud Run. Unauthenticated, no GCP calls."""
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok", "project": PROJECT, "auth_mode": AUTH_MODE})


@mcp.tool(annotations=READ_ONLY)
@logged_tool
def whoami() -> dict[str, Any]:
    """Who is signed in and which dataset/limits apply."""
    try:
        email, _ = current_user()
    except AuthError as e:
        return tool_error("whoami: auth failed", e, msg=str(e))
    log.info("whoami: user=%s auth_mode=%s dataset=%s.%s max_gb=%s max_rows=%s", email, AUTH_MODE,
             PROJECT, DATASET, gb(MAX_BYTES), MAX_ROWS)
    return {"ok": True, "user": email, "dataset": f"{PROJECT}.{DATASET}",
            "max_gb_per_query": gb(MAX_BYTES), "max_rows": MAX_ROWS}


@mcp.tool(annotations=READ_ONLY)
@logged_tool
def list_tables() -> dict[str, Any]:
    """List tables you can query with description, row count and partition column. Call this first."""
    try:
        _, client = bq_client()
        tables = []
        log.info("list_tables: listing %s.%s", PROJECT, DATASET)
        for item in client.list_tables(f"{PROJECT}.{DATASET}"):
            t = client.get_table(item.reference)
            part = t.time_partitioning.field if t.time_partitioning else None
            log.debug("list_tables: %s rows=%s partition=%s", t.table_id, t.num_rows, part)
            tables.append({"table": t.table_id, "description": t.description, "num_rows": t.num_rows,
                           "partition_column": part})
        log.info("list_tables: found %d table(s): %s", len(tables), [t["table"] for t in tables])
        return {"ok": True, "dataset": f"{PROJECT}.{DATASET}", "tables": tables}
    except Exception as e:
        return tool_error("list_tables", e)


@mcp.tool(annotations=READ_ONLY)
@logged_tool
def describe_table(table: str) -> dict[str, Any]:
    """Columns (name, type, description), partition and cluster columns of a table.
    Column descriptions explain codes and business meaning: read them before writing SQL."""
    try:
        fq = resolve_table(table)
        log.info("describe_table: %r resolved to %s", table, fq)
        _, client = bq_client()
        t = client.get_table(fq)
        log.info("describe_table: %s columns=%d rows=%s size_gb=%s partition=%s clusters=%s", fq,
                 len(t.schema), t.num_rows, gb(t.num_bytes or 0),
                 t.time_partitioning.field if t.time_partitioning else None, t.clustering_fields or [])
        return {
            "ok": True, "table": fq, "description": t.description, "num_rows": t.num_rows,
            "size_gb": gb(t.num_bytes or 0),
            "partition_column": t.time_partitioning.field if t.time_partitioning else None,
            "cluster_columns": t.clustering_fields or [],
            "columns": [{"name": f.name, "type": f.field_type, "mode": f.mode, "description": f.description}
                        for f in t.schema],
        }
    except Exception as e:
        return tool_error("describe_table", e, "Call list_tables for valid table names.")


@mcp.tool(annotations=READ_ONLY)
@logged_tool
def sample_rows(table: str, n: int = 5) -> dict[str, Any]:
    """A few real rows from a table. Free: reads storage directly, no query cost."""
    try:
        fq = resolve_table(table)
        n_eff = max(1, min(n, 20))
        log.info("sample_rows: %r resolved to %s, reading %d row(s) (requested %d)", table, fq, n_eff, n)
        _, client = bq_client()
        rows = to_rows(client.list_rows(fq, max_results=n_eff))
        log.info("sample_rows: got %d row(s) from %s", len(rows), fq)
        log.debug("sample_rows: rows=%s", short(rows))
        return {"ok": True, "table": fq, "rows": rows}
    except Exception as e:
        return tool_error("sample_rows", e)


@mcp.tool(annotations=READ_ONLY)
@logged_tool
def column_values(table: str, column: str, limit: int = 25) -> dict[str, Any]:
    """Most common distinct values of a column with counts (exact spellings for WHERE filters)."""
    try:
        fq = resolve_table(table)
        check_identifier(column, "column")
        email, client = bq_client()
    except Exception as e:
        return tool_error("column_values: setup", e)
    limit = max(1, min(limit, 100))
    sql = f"SELECT `{column}` AS value, COUNT(*) AS n FROM `{fq}` GROUP BY value ORDER BY n DESC LIMIT {limit}"
    log.info("column_values: %s.%s limit=%d", fq, column, limit)
    _, problem = guarded(client, sql)
    if problem:
        log.warning("column_values: guard rejected query: %s", problem.get("error"))
        return problem
    try:
        job, _, rows = execute(client, sql, email, limit)
        log.info("column_values: %d distinct value(s) returned (job=%s)", len(rows), job.job_id)
        return {"ok": True, "table": fq, "column": column, "values": rows}
    except Exception as e:
        return tool_error("column_values: execute", e)


@mcp.tool(annotations=READ_ONLY)
@logged_tool
def validate_sql(sql: str) -> dict[str, Any]:
    """Dry-run SQL without running it: syntax, read-only, allowed tables, GB and cost estimate.
    Call before run_sql."""
    try:
        _, client = bq_client()
    except Exception as e:
        return tool_error("validate_sql: setup", e)
    info, problem = guarded(client, sql)
    if problem:
        log.warning("validate_sql: INVALID: %s", problem.get("error"))
        return problem
    log.info("validate_sql: VALID type=%s est_gb=%s est_cost_usd=%s", info.statement_type, gb(info.bytes),
             cost_usd(info.bytes))
    return {"ok": True, "statement_type": info.statement_type, "estimated_gb": gb(info.bytes),
            "estimated_cost_usd": cost_usd(info.bytes), "tables": list(info.tables)}


@mcp.tool(annotations=READ_ONLY)
@logged_tool
async def run_sql(
    sql: str, ctx: Context, approval_id: str | None = None
) -> dict[str, Any] | mcp_types.InputRequiredResult:
    """Run a read-only SELECT. The user is shown the SQL, estimated GB and cost and must approve it.
    Up to BQ_MAX_ROWS rows come back inline; bigger results are exported to GCS and returned as links
    (with a preview of the first rows). Aggregate in SQL; don't pull raw rows to count or sum.

    If the response has needs_approval=true, SHOW the sql and estimate to the user, and only after
    they confirm, call run_sql again with the same sql and the returned approval_id."""
    try:
        email, creds = current_user()
        client = bigquery.Client(project=PROJECT, location=LOCATION, credentials=creds)
    except Exception as e:
        return tool_error("run_sql: setup", e)

    info, problem = await asyncio.to_thread(guarded, client, sql)
    if problem:
        log.warning("run_sql: guard rejected query: %s", problem.get("error"))
        return problem

    # ---------- 1. human approval ----------
    if not REQUIRE_APPROVAL:
        log.info("run_sql: REQUIRE_APPROVAL=false, skipping approval")
    elif approval_id:
        log.info("run_sql: approval_id supplied, verifying")
    if REQUIRE_APPROVAL and not approval_valid(approval_id, sql, email):
        decision = await ask_user_to_approve(ctx, sql, info, email)
        log.info("run_sql: approval decision=%s", decision if isinstance(decision, str) else type(decision).__name__)
        if not isinstance(decision, str):  # InputRequiredResult: client shows the prompt, then retries
            return decision
        if decision == "rejected":
            log.warning("run_sql: user REJECTED the query")
            return {"ok": False, "cancelled": True, "error": "The user did not approve this query.",
                    "hint": "Ask the user what to change instead of retrying the same SQL."}
        if decision == "unsupported":
            log.info("run_sql: issuing approval_id (ttl=%ss) for the agent to confirm with the user",
                     APPROVAL_TTL_S)
            return {
                "ok": False, "needs_approval": True, "sql": sql.strip(),
                "estimated_gb": gb(info.bytes), "estimated_cost_usd": cost_usd(info.bytes),
                "tables": list(info.tables), "approval_id": make_approval_id(sql, email),
                "expires_in_s": APPROVAL_TTL_S,
                "hint": "Show this SQL and estimate to the user. If they approve, call run_sql again "
                        "with the same sql and this approval_id.",
            }

    # ---------- 2. execute ----------
    t0 = time.perf_counter()
    try:
        job, it, rows = await asyncio.to_thread(execute, client, sql, email, MAX_ROWS)
    except Exception as e:
        return tool_error("run_sql: execute", e, "Fix the SQL and try again.")
    total = it.total_rows or 0
    scanned = job.total_bytes_processed or 0
    base = {"ok": True, "sql": sql.strip(), "total_rows": total, "gb_processed": gb(scanned),
            "cost_usd": cost_usd(scanned), "job_id": job.job_id}

    # ---------- 3. small result -> inline ----------
    if total <= MAX_ROWS:
        secs = round(time.perf_counter() - t0, 2)
        log.info("run_sql: job=%s gb=%s cost_usd=%s rows=%s secs=%s delivery=inline", job.job_id, gb(scanned),
                 cost_usd(scanned), total, secs)
        return {**base, "delivery": "inline", "row_count": len(rows), "rows": rows, "seconds": secs}

    # ---------- 4. large result -> GCS ----------
    log.info("run_sql: %s rows > MAX_ROWS=%s, result needs export", total, MAX_ROWS)
    preview = rows[:20]
    if not EXPORT_BUCKET:
        log.warning("run_sql: EXPORT_BUCKET not set, returning truncated result (%d of %s rows)", len(rows), total)
        return {**base, "delivery": "truncated", "row_count": len(rows), "rows": rows, "truncated": True,
                "hint": "Result is larger than the row limit and EXPORT_BUCKET is not set. "
                        "Aggregate further or add LIMIT."}
    try:
        export = await asyncio.to_thread(export_to_gcs, client, job, creds)
    except Exception as e:
        tool_error("run_sql: export to GCS failed, returning truncated result", e)
        return {**base, "delivery": "truncated", "row_count": len(rows), "rows": rows, "truncated": True,
                "export_error": bq_msg(e)}
    secs = round(time.perf_counter() - t0, 2)
    log.info("run_sql: job=%s gb=%s cost_usd=%s rows=%s secs=%s delivery=gcs folder=%s", job.job_id,
             gb(scanned), cost_usd(scanned), total, secs, export["folder"])
    return {**base, "delivery": "gcs", "preview_rows": preview, "export": export, "seconds": secs,
            "message": f"{total:,} rows is more than {MAX_ROWS}; full result exported to GCS. "
                       "Give the user the download link(s) and summarise the preview."}


# ═════════════════════════════ verified examples (Vertex AI RAG) ═════════════════════════════
SAVE_KEY = "save_example"
SAVE_CHOICES = ["Yes, the answer is correct", "No"]


def _rag():
    """rag_store.py lives next to this file. Examples are shared, so it uses the server's credentials."""
    import rag_store

    if not (rag_store.CORPUS_NAME and rag_store.BUCKET):
        raise RuntimeError("RAG not configured: set RAG_CORPUS_NAME and EXAMPLES_BUCKET in .env")
    return rag_store


@mcp.tool(annotations=READ_ONLY)
@logged_tool
async def search_examples(question: str, k: int = 3) -> dict[str, Any]:
    """Find verified question -> SQL examples similar to the user's question. Call this FIRST.
    If an example matches closely, adapt its SQL (dates, filters) instead of writing from scratch.
    Examples also carry notes on business rules (e.g. how revenue is defined)."""
    k_eff = max(1, min(k, 5))
    try:
        examples = await asyncio.to_thread(_rag().search_examples, question, k_eff)
    except Exception as e:
        return tool_error("search_examples", e, "Continue without examples.", msg=f"Example search unavailable: {e}")
    log.info("search_examples: %d match(es) for k=%d", len(examples), k_eff)
    for i, ex in enumerate(examples, 1):
        log.info("search_examples: #%d score=%s question=%r source=%s", i, ex.get("score"),
                 short(ex.get("question", ""), 200), ex.get("source_uri"))
    return {"ok": True, "count": len(examples), "examples": [
        {k_: ex.get(k_) for k_ in ("question", "sql", "notes", "tables", "score", "source_uri") if ex.get(k_)}
        for ex in examples
    ]}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
                       "openWorldHint": False})
@logged_tool
async def save_example(question: str, sql: str, ctx: Context, notes: str = "",
                       approval_id: str | None = None) -> dict[str, Any] | mcp_types.InputRequiredResult:
    """Save a question -> SQL pair as a verified example for future questions.
    ONLY call after the user has confirmed the answer was correct. The user is asked to confirm
    again before saving. If the response has needs_confirmation=true, show the question and SQL
    to the user and call again with approval_id only if they confirm."""
    try:
        email, creds = current_user()
        client = bigquery.Client(project=PROJECT, location=LOCATION, credentials=creds)
    except Exception as e:
        return tool_error("save_example: setup", e)

    # never store SQL that doesn't validate (read-only, in scope, within budget)
    info, problem = await asyncio.to_thread(guarded, client, sql)
    if problem:
        log.warning("save_example: SQL failed validation, not saving: %s", problem.get("error"))
        return {**problem, "hint": "Only valid, approved SELECT queries can be saved."}

    bind = f"{question.strip()}\n{sql.strip()}"
    if not approval_valid(approval_id, bind, email):
        decision = await confirm(
            ctx, key=SAVE_KEY, title="Is this answer correct?", choices=SAVE_CHOICES, bind=bind, email=email,
            message=(f"Save this as a verified example for future questions?\n\n"
                     f"Question: {question.strip()}\n\n{sql.strip()}"),
        )
        log.info("save_example: confirmation decision=%s",
                 decision if isinstance(decision, str) else type(decision).__name__)
        if not isinstance(decision, str):
            return decision
        if decision == "rejected":
            log.warning("save_example: user did NOT confirm, nothing saved")
            return {"ok": False, "saved": False, "error": "The user did not confirm; nothing was saved."}
        if decision == "unsupported":
            log.info("save_example: issuing approval_id (ttl=%ss) for the agent to confirm", APPROVAL_TTL_S)
            return {"ok": False, "needs_confirmation": True, "question": question.strip(), "sql": sql.strip(),
                    "approval_id": make_approval_id(bind, email), "expires_in_s": APPROVAL_TTL_S,
                    "hint": "Ask the user whether the answer was correct. If yes, call save_example again "
                            "with the same question, sql and this approval_id."}

    tables = sorted({t.split(".")[-1] for t in info.tables})
    log.info("save_example: saving question=%r tables=%s notes=%r", short(question, 200), tables, short(notes, 200))
    try:
        result = await asyncio.to_thread(_rag().save_example, question, sql,
                                         verified_by=email, tables=tables, notes=notes)
    except Exception as e:
        return tool_error("save_example: RAG save", e, msg=f"Could not save example: {e}")
    log.info("save_example: saved %s (imported=%s)", result["gcs_uri"], result.get("imported"))
    return {"ok": True, **result}


# ═════════════════════════════ entrypoint ═════════════════════════════
if __name__ == "__main__":
    # third-party libraries (fastmcp, google) log through the root logger; ours use the 'bq_mcp' tree
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,  # stderr: stdout is the stdio channel
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log.info("starting bq_mcp: project=%s dataset=%s location=%s auth=%s approval=%s max_gb=%s max_rows=%s "
             "export_bucket=%s log_level=%s log_file=%s", PROJECT, DATASET, LOCATION, AUTH_MODE,
             REQUIRE_APPROVAL, gb(MAX_BYTES), MAX_ROWS, EXPORT_BUCKET or "-", LOG_LEVEL, LOG_FILE or "-")
    if not PROJECT:
        sys.exit("Set BQ_PROJECT in .env or the environment")
    if "--stdio" in sys.argv:
        if AUTH_MODE != "none":
            sys.exit("stdio is local-only: set AUTH_MODE=none (OAuth needs HTTP)")
        mcp.run()
    else:
        mcp.run(transport="http", host=HOST, port=PORT)
