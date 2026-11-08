"""
Verified question -> SQL examples, stored as .sql files in GCS and indexed by Vertex AI RAG Engine.

Each example is ONE <id>.sql.txt file (.txt so RAG Engine's parser accepts it):
question + metadata as comments, then the SQL.
GCS is the source of truth; the RAG corpus indexes those same files, so one retrieval
returns the complete example (question + SQL) to use as a few-shot prompt.

Install:
    uv add google-cloud-aiplatform google-cloud-storage

Commands (run from the folder containing .env):
    uv run rag_store.py info                       # project number, your login, RAG service account
    uv run rag_store.py setup                      # create the corpus once, prints RAG_CORPUS_NAME
    uv run rag_store.py seed verified_queries      # upload + index the 20 seed examples
    uv run rag_store.py search "busiest borough last month"
    uv run rag_store.py list

.env keys used (in addition to the MCP server's):
    BQ_PROJECT=your-gcp-project-id
    RAG_LOCATION=us-central1                 # Serverless mode (no allowlist) is us-central1 only
    RAG_CORPUS_NAME=projects/.../locations/us-central1/ragCorpora/123   # printed by `setup`
    EXAMPLES_BUCKET=your-bucket-name         # can be the same bucket as EXPORT_BUCKET
    EXAMPLES_PREFIX=verified-queries
    RAG_MAX_DISTANCE=0.6                     # lower = stricter match (cosine distance)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os
import re
import sys
import time
import warnings
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", message=".*vertexai.rag.*deprecated.*")

# child of the MCP server's "bq_mcp" logger, so lines carry the same call id / user
log = logging.getLogger("bq_mcp.rag")


# ───────────────────────── config ─────────────────────────
def _load_dotenv(path: Path) -> None:
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(Path(__file__).with_name(".env"))
_load_dotenv(Path.cwd() / ".env")

PROJECT = os.getenv("BQ_PROJECT", "")
RAG_LOCATION = os.getenv("RAG_LOCATION", "us-central1")
CORPUS_NAME = os.getenv("RAG_CORPUS_NAME", "")
BUCKET = os.getenv("EXAMPLES_BUCKET") or os.getenv("EXPORT_BUCKET", "")
PREFIX = os.getenv("EXAMPLES_PREFIX", "verified-queries").strip("/")
MAX_DISTANCE = float(os.getenv("RAG_MAX_DISTANCE", "0.6"))
EMBEDDING_MODEL = os.getenv("RAG_EMBEDDING_MODEL", "publishers/google/models/text-embedding-005")

HEADER_KEYS = ["question", "tables", "tags", "difficulty", "notes", "verified_by", "verified_at"]


def _init():
    import vertexai

    if not PROJECT:
        raise RuntimeError("Set BQ_PROJECT in .env")
    vertexai.init(project=PROJECT, location=RAG_LOCATION)
    from vertexai import rag

    return rag


def _corpus() -> str:
    if not CORPUS_NAME:
        raise RuntimeError("Set RAG_CORPUS_NAME in .env (run `rag_store.py setup` first)")
    return CORPUS_NAME


def _bucket():
    from google.cloud import storage

    if not BUCKET:
        raise RuntimeError("Set EXAMPLES_BUCKET (or EXPORT_BUCKET) in .env")
    return storage.Client(project=PROJECT).bucket(BUCKET)


# ───────────────────────── file format ─────────────────────────
def normalize_question(q: str) -> str:
    return re.sub(r"\s+", " ", q.strip().lower()).rstrip("?.! ")


FILE_EXT = ".sql.txt"   # RAG Engine's default parser skips unknown extensions like .sql


def example_filename(question: str) -> str:
    return f"{example_id(question)}{FILE_EXT}"


def example_id(question: str) -> str:
    """Same question (ignoring case/spacing/punctuation at the end) -> same file -> update, not duplicate."""
    return hashlib.sha1(normalize_question(question).encode()).hexdigest()[:16]


def format_example(question: str, sql: str, *, tables: list[str] | None = None, tags: str = "",
                   difficulty: str = "", notes: str = "", verified_by: str = "user") -> str:
    meta = {
        "question": question.strip().replace("\n", " "),
        "tables": ", ".join(tables or []),
        "tags": tags,
        "difficulty": difficulty,
        "notes": notes.replace("\n", " "),
        "verified_by": verified_by,
        "verified_at": dt.date.today().isoformat(),
    }
    header = "\n".join(f"-- {k}: {v}" for k, v in meta.items() if v)
    return f"{header}\n{sql.strip().rstrip(';')};\n"


def parse_example(text: str) -> dict[str, Any]:
    """Split a stored example back into metadata + SQL."""
    meta, sql_lines = {}, []
    for line in text.splitlines():
        m = re.match(r"^--\s*(\w+):\s*(.*)$", line)
        if m and m.group(1) in HEADER_KEYS and not sql_lines:
            meta[m.group(1)] = m.group(2).strip()
        else:
            sql_lines.append(line)
    return {**meta, "sql": "\n".join(sql_lines).strip()}


# ───────────────────────── GCS ─────────────────────────
def upload_text(name: str, text: str) -> str:
    blob = _bucket().blob(f"{PREFIX}/{name}")
    # text/plain so RAG Engine's default parser treats .sql as a text file
    t0 = time.perf_counter()
    blob.upload_from_string(text, content_type="text/plain; charset=utf-8")
    uri = f"gs://{BUCKET}/{blob.name}"
    log.info("rag: uploaded %s (%d bytes) in %.2fs", uri, len(text.encode()), time.perf_counter() - t0)
    return uri


# ───────────────────────── RAG corpus ─────────────────────────
def _engine_config_url() -> str:
    return (f"https://{RAG_LOCATION}-aiplatform.googleapis.com/v1beta1/"
            f"projects/{PROJECT}/locations/{RAG_LOCATION}/ragEngineConfig")


def project_number() -> str:
    """Look up the numeric project number (service agent emails use it, not the project id)."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    r = AuthorizedSession(creds).get(f"https://cloudresourcemanager.googleapis.com/v3/projects/{PROJECT}")
    r.raise_for_status()
    return r.json()["name"].split("/")[-1]  # "projects/616661544234"


def rag_service_account() -> str:
    """Google-managed account RAG Engine uses to read your bucket and write to Vector Search."""
    return f"service-{project_number()}@gcp-sa-vertex-rag.iam.gserviceaccount.com"


def print_info() -> None:
    import google.auth

    creds, adc_project = google.auth.default()
    sa = rag_service_account()
    print(f"Project:              {PROJECT}  (number {project_number()})")
    print(f"Your login (ADC):     {getattr(creds, 'service_account_email', None) or 'user credentials'}"
          f"  quota project: {getattr(creds, 'quota_project_id', None) or adc_project}")
    print(f"RAG location:         {RAG_LOCATION}")
    print(f"Examples bucket:      gs://{BUCKET}/{PREFIX}/")
    print(f"RAG service account:  {sa}")
    print("\nGrant it read access to the bucket (PowerShell):")
    print(f'  gcloud storage buckets add-iam-policy-binding gs://{BUCKET} `\n'
          f'    --member="serviceAccount:{sa}" --role="roles/storage.objectViewer"')


def ensure_serverless() -> None:
    """New projects can't use Spanner mode outside an allowlist; Serverless mode (us-central1) has
    no allowlist and no extra charge. This is a project-level setting, so it only needs to run once.
    Uses REST directly because older SDK versions have no Serverless option."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    if RAG_LOCATION != "us-central1":
        raise RuntimeError("Serverless mode is only available in us-central1: set RAG_LOCATION=us-central1")
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    session = AuthorizedSession(creds)

    current = session.get(_engine_config_url())
    current.raise_for_status()
    if "serverless" in (current.json().get("ragManagedDbConfig") or {}):
        print("RAG Engine is already in Serverless mode.")
        return

    print("Switching RAG Engine to Serverless mode...")
    resp = session.patch(_engine_config_url(), json={"ragManagedDbConfig": {"serverless": {}}})
    if not resp.ok:
        raise RuntimeError(f"Could not switch to Serverless mode: {resp.status_code} {resp.text}")

    # the PATCH returns a long-running operation; wait until the mode reads back as serverless
    for _ in range(30):
        time.sleep(5)
        cfg = session.get(_engine_config_url()).json().get("ragManagedDbConfig") or {}
        if "serverless" in cfg:
            print("RAG Engine is now in Serverless mode.")
            return
    raise RuntimeError("Timed out waiting for Serverless mode; check the RAG Engine page in the console.")


def setup(display_name: str = "nyc-taxi-verified-sql") -> str:
    ensure_serverless()
    rag = _init()
    for c in rag.list_corpora():
        if c.display_name == display_name:
            print("Corpus already exists.")
            return c.name
    corpus = rag.create_corpus(
        display_name=display_name,
        description="Verified natural-language question -> BigQuery SQL examples (NYC taxi)",
        backend_config=rag.RagVectorDbConfig(
            rag_embedding_model_config=rag.RagEmbeddingModelConfig(
                vertex_prediction_endpoint=rag.VertexPredictionEndpoint(publisher_model=EMBEDDING_MODEL)
            )
        ),
    )
    return corpus.name


def _import(rag, paths: list[str]):
    # One example per chunk: files are small, so a large chunk keeps question + SQL together.
    log.info("rag: importing %d file(s) into %s", len(paths), _corpus())
    t0 = time.perf_counter()
    resp = rag.import_files(
        _corpus(), paths,
        transformation_config=rag.TransformationConfig(
            chunking_config=rag.ChunkingConfig(chunk_size=1024, chunk_overlap=0)),
        max_embedding_requests_per_min=600,
    )
    log.info("rag: import done in %.2fs imported=%s skipped=%s failed=%s", time.perf_counter() - t0,
             resp.imported_rag_files_count, resp.skipped_rag_files_count,
             getattr(resp, "failed_rag_files_count", 0) or 0)
    return resp


def _delete_existing(rag, filename: str) -> None:
    """Re-importing a changed file: remove the old indexed copy first so we don't keep both."""
    for f in rag.list_files(_corpus()):
        if f.display_name == filename:
            log.info("rag: deleting previous indexed copy %s (%s)", filename, f.name)
            rag.delete_file(f.name)


def seed(folder: str = "verified_queries") -> None:
    """Upload every .sql in `folder` (filling in {{PROJECT}}) and index them in one import."""
    rag = _init()
    files = sorted(Path(folder).glob("*.sql"))
    if not files:
        raise SystemExit(f"No .sql files in {Path(folder).resolve()}")
    existing = {f.display_name: f.name for f in rag.list_files(_corpus())}
    uris: list[str] = []
    for p in files:
        ex = parse_example(p.read_text(encoding="utf-8").replace("{{PROJECT}}", PROJECT))
        name = example_filename(ex["question"])
        text = format_example(ex["question"], ex["sql"],
                              tables=[t.strip() for t in ex.get("tables", "").split(",") if t.strip()],
                              tags=ex.get("tags", ""), difficulty=ex.get("difficulty", ""),
                              notes=ex.get("notes", ""), verified_by=ex.get("verified_by", "seed"))
        uris.append(upload_text(name, text))
        if name in existing:
            rag.delete_file(existing[name])
        print(f"uploaded {p.name} -> {name}")
    totals = {"imported": 0, "skipped": 0, "failed": 0}
    for i in range(0, len(uris), 25):  # import explicit file URIs, in batches
        resp = _import(rag, uris[i:i + 25])
        totals["imported"] += resp.imported_rag_files_count
        totals["skipped"] += resp.skipped_rag_files_count
        totals["failed"] += getattr(resp, "failed_rag_files_count", 0) or 0
    print(f"imported: {totals['imported']}, skipped: {totals['skipped']}, failed: {totals['failed']}")
    if totals["imported"] == 0:
        try:
            sa = rag_service_account()
        except Exception:
            sa = "service-<PROJECT_NUMBER>@gcp-sa-vertex-rag.iam.gserviceaccount.com"
        print(f"Nothing was imported. Make sure the RAG service account can read gs://{BUCKET}:\n  {sa}\n"
              "Run `rag_store.py info` for the exact grant command.")


def search_examples(question: str, k: int = 3, max_distance: float = MAX_DISTANCE) -> list[dict[str, Any]]:
    """Top-k verified examples similar to `question`, as dicts with question, sql, notes, score."""
    rag = _init()
    log.info("rag: retrieval k=%d max_distance=%s question=%r", k, max_distance, question[:300])
    t0 = time.perf_counter()
    resp = rag.retrieval_query(
        text=question,
        rag_resources=[rag.RagResource(rag_corpus=_corpus())],
        rag_retrieval_config=rag.RagRetrievalConfig(
            top_k=k, filter=rag.Filter(vector_distance_threshold=max_distance)),
    )
    out = []
    for c in resp.contexts.contexts:
        ex = parse_example(c.text)
        ex["source_uri"] = c.source_uri
        ex["score"] = round(float(getattr(c, "score", 0) or getattr(c, "distance", 0) or 0), 4)
        out.append(ex)
    log.info("rag: retrieval returned %d context(s) in %.2fs", len(out), time.perf_counter() - t0)
    return out


def save_example(question: str, sql: str, *, verified_by: str, tables: list[str] | None = None,
                 notes: str = "") -> dict[str, Any]:
    """Store a user-confirmed question -> SQL pair. Same question overwrites the previous version."""
    rag = _init()
    name = example_filename(question)
    log.info("rag: saving example id=%s verified_by=%s tables=%s", name, verified_by, tables)
    uri = upload_text(name, format_example(question, sql, tables=tables, notes=notes, verified_by=verified_by))
    _delete_existing(rag, name)
    resp = _import(rag, [uri])
    return {"saved": True, "gcs_uri": uri, "imported": resp.imported_rag_files_count,
            "note": "Indexing can take a minute before it shows up in search."}


def list_examples() -> list[dict[str, Any]]:
    rag = _init()
    return [{"display_name": f.display_name, "name": f.name} for f in rag.list_files(_corpus())]


# ───────────────────────── CLI ─────────────────────────
if __name__ == "__main__":
    cmd, *args = sys.argv[1:] or ["help"]
    if cmd == "info":
        print_info()
    elif cmd == "setup":
        name = setup()
        print(f"\nAdd this to .env:\nRAG_CORPUS_NAME={name}")
        print(f"RAG service account: {rag_service_account()}")
    elif cmd == "seed":
        seed(args[0] if args else "verified_queries")
    elif cmd == "search":
        for i, ex in enumerate(search_examples(" ".join(args)), 1):
            print(f"\n#{i} score={ex['score']}  {ex.get('question')}\n{ex['sql']}")
    elif cmd == "list":
        for f in list_examples():
            print(f["display_name"])
    else:
        print(__doc__)
