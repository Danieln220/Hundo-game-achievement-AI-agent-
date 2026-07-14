"""DeepSeek LLM client via OpenAI-compatible API.

All nodes import call_llm() from here — one place to change the client,
timeouts, or model defaults. A per-call timeout plus retry/backoff keeps a
single hiccup (rate limit, transient 5xx, slow response) from killing a run —
important once many users share one API key.

Token accounting (Step 19.4 / 22.6): every call records its usage into the
run's active COLLECTOR (a plain dict held in a contextvar, so concurrent
requests never mix). LangGraph's executor copies contextvars into its worker
threads; our own ThreadPoolExecutor/Thread spawns must propagate explicitly
via call_with_usage(). Totals land on the result as `llm_usage` → query_log.
"""
import contextvars
import threading
import time

try:
    # Dev boxes behind SSL interception (corp proxy/AV) fail httpx's certificate
    # check against api.deepseek.com; trusting the OS certificate store fixes it.
    # Harmless in prod (the Debian image ships system CAs) and a no-op when the
    # package is absent. truststore is already pinned in requirements.txt.
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from openai import OpenAI, APIConnectionError, APITimeoutError, RateLimitError, InternalServerError

from config import (
    LLM_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL_FLASH, LLM_TIMEOUT_SECONDS,
    LLM_MAX_TOKENS,
)

_client = OpenAI(
    api_key=LLM_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    timeout=LLM_TIMEOUT_SECONDS,
)

# Transient errors worth retrying with backoff.
_RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)
_MAX_ATTEMPTS = 3


# ── Token usage accounting (19.4 / 22.6) ──────────────────────────────────────

_usage_var: contextvars.ContextVar = contextvars.ContextVar("hundo_llm_usage", default=None)
_usage_lock = threading.Lock()


def new_usage() -> dict:
    """A fresh per-run usage collector. cache_hit_tokens is DeepSeek's
    prompt_cache_hit_tokens — the prefix-cache hit portion of prompt_tokens."""
    return {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cache_hit_tokens": 0}


def use_usage(collector: dict | None):
    """Activate a collector for the current context; returns the reset token."""
    return _usage_var.set(collector)


def reset_usage(token) -> None:
    _usage_var.reset(token)


def current_usage() -> dict | None:
    return _usage_var.get()


def call_with_usage(collector: dict | None, fn, *args, **kwargs):
    """Run fn with `collector` active — for threads WE spawn (plain
    Thread/ThreadPoolExecutor don't inherit contextvars)."""
    if collector is None:
        return fn(*args, **kwargs)
    token = _usage_var.set(collector)
    try:
        return fn(*args, **kwargs)
    finally:
        _usage_var.reset(token)


def _record(usage_obj) -> None:
    """Accumulate one response's usage into the active collector (no-op without one)."""
    col = _usage_var.get()
    if col is None or usage_obj is None:
        return
    with _usage_lock:
        col["llm_calls"] += 1
        col["prompt_tokens"] += getattr(usage_obj, "prompt_tokens", 0) or 0
        col["completion_tokens"] += getattr(usage_obj, "completion_tokens", 0) or 0
        col["cache_hit_tokens"] += getattr(usage_obj, "prompt_cache_hit_tokens", 0) or 0


def call_llm(prompt: str, model: str = DEEPSEEK_MODEL_FLASH, system: str = "",
             on_token=None, max_tokens: int | None = None) -> str:
    """Call DeepSeek and return the stripped text response.

    temperature=0 keeps code generation deterministic across retries.
    Retries transient failures with exponential backoff (1s, 2s, 4s).

    If `on_token` is given, the response is STREAMED: each text delta is passed to
    on_token(delta) as it arrives (for live UI streaming) and the full text is still
    returned. Streaming failures fall back to the normal buffered call.

    `max_tokens` bounds the response (defaults to the global LLM_MAX_TOKENS ceiling
    so a runaway generation can't burn unbounded quota)."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    max_tokens = max_tokens or LLM_MAX_TOKENS

    if on_token is not None:
        try:
            parts = []
            usage_obj = None
            stream = _client.chat.completions.create(
                model=model, messages=messages, temperature=0, stream=True,
                max_tokens=max_tokens,
                # Ask for a final usage chunk so streamed calls are counted too.
                stream_options={"include_usage": True},
            )
            for chunk in stream:
                u = getattr(chunk, "usage", None)
                if u:
                    usage_obj = u
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    parts.append(delta)
                    try:
                        on_token(delta)
                    except Exception:
                        pass
            text = "".join(parts).strip()
            if text:
                _record(usage_obj)
                return text
        except Exception:
            pass  # fall back to the buffered path below

    last_exc = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = _client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
                max_tokens=max_tokens,
            )
            _record(getattr(resp, "usage", None))
            return resp.choices[0].message.content.strip()
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(2 ** attempt)

    raise RuntimeError(
        f"LLM call failed after {_MAX_ATTEMPTS} attempts: {last_exc}"
    ) from last_exc
