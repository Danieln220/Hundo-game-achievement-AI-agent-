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

MAX_RETRIES = 3            # bound the self-correction loop (total code attempts)
EXEC_TIMEOUT_SECONDS = 10  # hard timeout for sandboxed code
EXEC_MEMORY_MB = 512       # address-space cap for sandboxed code (POSIX only)
EXEC_MAX_OUTPUT_CHARS = 20000  # truncate sandbox result so a huge dump can't flood the UI
LLM_TIMEOUT_SECONDS = 60   # per-call timeout for the LLM client
SNAPSHOT_DIR = "data/snapshot"  # base dir; each user gets a <steam_id>/ subdir


def missing_secrets() -> list[str]:
    """Return the names of required secrets that are not set.

    Call at startup (or before a run/build) to fail with a clear message
    instead of a cryptic error deep inside a request."""
    missing = []
    if not STEAM_API_KEY:
        missing.append("STEAM_API_KEY")
    if not LLM_API_KEY:
        missing.append("LLM_API_KEY")
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
