"""DeepSeek LLM client via OpenAI-compatible API.

All nodes import call_llm() from here — one place to change the client,
timeouts, or model defaults. A per-call timeout plus retry/backoff keeps a
single hiccup (rate limit, transient 5xx, slow response) from killing a run —
important once many users share one API key.
"""
import time

from openai import OpenAI, APIConnectionError, APITimeoutError, RateLimitError, InternalServerError

from config import LLM_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL_FLASH, LLM_TIMEOUT_SECONDS

_client = OpenAI(
    api_key=LLM_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    timeout=LLM_TIMEOUT_SECONDS,
)

# Transient errors worth retrying with backoff.
_RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)
_MAX_ATTEMPTS = 3


def call_llm(prompt: str, model: str = DEEPSEEK_MODEL_FLASH, system: str = "") -> str:
    """Call DeepSeek and return the stripped text response.

    temperature=0 keeps code generation deterministic across retries.
    Retries transient failures with exponential backoff (1s, 2s, 4s)."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    last_exc = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = _client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
            )
            return resp.choices[0].message.content.strip()
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(2 ** attempt)

    raise RuntimeError(
        f"LLM call failed after {_MAX_ATTEMPTS} attempts: {last_exc}"
    ) from last_exc
