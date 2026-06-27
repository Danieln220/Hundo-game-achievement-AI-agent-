"""Central config. Reads secrets from environment variables via os.environ
so the SAME code works on Streamlit Cloud now and Render later — no
platform-specific secrets API in the code."""
import os

# Use the OS trust store for SSL (fixes cert errors on some Windows machines).
# Wrapped so a missing/incompatible truststore never breaks Linux deploys.
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

from dotenv import load_dotenv

load_dotenv()  # loads .env locally; on a platform the env vars are injected

STEAM_API_KEY = os.environ.get("STEAM_API_KEY", "")
STEAM_ID = os.environ.get("STEAM_ID", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")  # web search for how-to guides
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL_PRO   = os.environ.get("DEEPSEEK_MODEL_PRO",   "deepseek-v4-pro")    # write_code, finalize, roadmap
DEEPSEEK_MODEL_FLASH = os.environ.get("DEEPSEEK_MODEL_FLASH", "deepseek-v4-flash")  # planner, router, validate

# Comma-separated allowed origins for the FastAPI backend (React phase).
# Default "*" for local dev; tighten to the frontend's domain in production.
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]

MAX_RETRIES = 3            # bound the self-correction loop (total code attempts)
# Hard timeout for sandboxed code. Env-overridable: on a throttled host (e.g.
# Render free tier = 0.1 CPU) matplotlib chart rendering can exceed 10s — bump
# EXEC_TIMEOUT_SECONDS there so charts/audit don't silently time out.
EXEC_TIMEOUT_SECONDS = int(os.environ.get("EXEC_TIMEOUT_SECONDS", "10"))
# Virtual-address-space (RLIMIT_AS) cap for sandboxed code (POSIX only; no-op on
# Windows). This is VIRTUAL memory, not RSS — pandas/numpy/matplotlib reserve large
# virtual arenas they never physically use, so a too-low value spuriously
# MemoryErrors on Linux even with tiny data. 2 GB virtual ≠ 2 GB RAM. Env-overridable.
EXEC_MEMORY_MB = int(os.environ.get("EXEC_MEMORY_MB", "2048"))
EXEC_MAX_OUTPUT_CHARS = 20000  # truncate sandbox result so a huge dump can't flood the UI
LLM_TIMEOUT_SECONDS = 60   # per-call timeout for the LLM client
SNAPSHOT_DIR = "data/snapshot"  # base dir; each user gets a <steam_id>/ subdir

# Disposable charts: generated PNGs are regenerable from the snapshot, so the API
# sweeps data/charts/ to keep it from growing unbounded (one file per question).
# Deletes files older than the TTL, then caps the dir to the newest N files.
CHART_TTL_HOURS = float(os.environ.get("CHART_TTL_HOURS", "6"))
CHART_MAX_FILES = int(os.environ.get("CHART_MAX_FILES", "50"))

# ── Object storage (Step 15.2) ────────────────────────────────────────────────
# Charts + snapshot blobs go to Supabase Storage when BOTH of these are set;
# otherwise everything stays on the local filesystem exactly as before (dev loop
# + eval untouched). Driven over the Supabase Storage REST API — no new dep.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SUPABASE_BUCKET_CHARTS = os.environ.get("SUPABASE_BUCKET_CHARTS", "hundo-charts")        # public
SUPABASE_BUCKET_SNAPSHOTS = os.environ.get("SUPABASE_BUCKET_SNAPSHOTS", "hundo-snapshots")  # private
USE_SUPABASE_STORAGE = bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)

# Lean Postgres (Step 15.5) — identity + snapshot metadata + usage log. Reuses the
# same Supabase project/creds via its PostgREST API (no DB driver dep). No-op when
# unset; best-effort writes never block a request. Tables: steam_user, snapshot,
# query_log (create once via Supabase SQL editor — see DEPLOY.md). NO auth/accounts.
USE_DB = bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)

# ── Redis (Upstash) — rate-limit + snapshot build-lock (Step 15.3) ────────────
# Used when BOTH are set; otherwise an in-memory fallback runs (fine for single-
# process dev). Driven over Upstash's REST API with `requests` — no new dep.
UPSTASH_REDIS_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_REDIS_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
USE_REDIS = bool(UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN)

# Per-IP rate limits (requests per window). All env-overridable.
RATE_LIMIT_ASK_PER_MIN = int(os.environ.get("RATE_LIMIT_ASK_PER_MIN", "15"))
RATE_LIMIT_ASK_PER_DAY = int(os.environ.get("RATE_LIMIT_ASK_PER_DAY", "150"))
RATE_LIMIT_SESSION_PER_MIN = int(os.environ.get("RATE_LIMIT_SESSION_PER_MIN", "10"))

# Snapshot build-lock TTL (seconds). SHORT + heartbeat (Step 15.6): the builder
# refreshes the lock every progress tick, so a live build keeps it; a DEAD build
# (killed/redeployed mid-flight) lets it expire within this window so a new build
# can take over — instead of a waiter sitting at 0/0 for the old 600s.
SNAPSHOT_LOCK_TTL = int(os.environ.get("SNAPSHOT_LOCK_TTL", "90"))
# Max time a concurrent caller waits for another worker's in-flight build before
# giving up and rebuilding itself (safety cap; normally exits as soon as the lock
# clears). Longer than any real build.
SNAPSHOT_WAIT_MAX = int(os.environ.get("SNAPSHOT_WAIT_MAX", "900"))

# Snapshot cache GC (Step 15.6): per-user snapshots not rebuilt in this many days
# are swept (blobs + DB row + local cache); they regenerate on the user's next
# visit. Identity (steam_user) + usage (query_log) are kept. Needs the DB (uses
# snapshot.built_at), so cleanup is skipped when USE_DB is false.
SNAPSHOT_TTL_DAYS = float(os.environ.get("SNAPSHOT_TTL_DAYS", "30"))


def missing_secrets() -> list[str]:
    """Return the names of required secrets that are not set.

    Call at startup (or before a run/build) to fail with a clear message
    instead of a cryptic error deep inside a request."""
    missing = []
    if not STEAM_API_KEY:
        missing.append("STEAM_API_KEY")
    if not LLM_API_KEY:
        missing.append("LLM_API_KEY")
    if not TAVILY_API_KEY:
        missing.append("TAVILY_API_KEY")  # how-to + time-to-complete degrade badly without it
    return missing


# LangSmith tracing — OPT-IN only. Set HUNDO_TRACING=true (and a LANGSMITH_API_KEY)
# to enable. Off by default so multi-user prod doesn't ship user data to LangSmith
# or burn quota. Must be set before langgraph/langchain are imported.
LANGSMITH_API_KEY = os.environ.get("LANGSMITH_API_KEY", "")
_tracing_on = (
    os.environ.get("HUNDO_TRACING", "false").lower() == "true"
    and bool(LANGSMITH_API_KEY)
)
os.environ["LANGSMITH_TRACING"]    = "true" if _tracing_on else "false"
os.environ["LANGCHAIN_TRACING_V2"] = "true" if _tracing_on else "false"
os.environ.setdefault("LANGCHAIN_PROJECT", "hundo")
