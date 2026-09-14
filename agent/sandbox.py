"""Sandboxed execution of model-written pandas code. SECURITY-CRITICAL.

This runs LLM-generated code. Isolation layers (Steps 3, 9.5, 23.1):
  1. Separate subprocess — a crash or hang can't take down the main process
  2. Hard timeout — EXEC_TIMEOUT_SECONDS kills the subprocess if it stalls
  3. SCRUBBED environment — only an allowlist of harmless vars is forwarded; no
     API keys, storage or Redis credentials ever reach the child (23.1)
  4. Interpreter flags -I -B -X utf8 — ignore PYTHON* env, no bytecode, UTF-8 I/O
  5. Parent hydrates the snapshot; the child reads ONE local directory through
     the dependency-free data_layer.frames and never imports config/storage
  6. Inside the runner: AST dunder gate, allowlisted importer, curated builtins,
     file/URL chokepoints (builtins.open + pandas io.common), RLIMIT_AS on POSIX
  7. Output protocol: the LAST stdout line is the JSON envelope; print() inside
     the sandbox is captured, so it can no longer corrupt the result

Honest limit: this is Python-level containment, not an OS sandbox. After 23.1
the child holds nothing worth stealing and its CPU/memory are bounded; true
isolation (container per exec, or the DuckDB engine) remains a later step.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

from config import EXEC_TIMEOUT_SECONDS, EXEC_MEMORY_MB, EXEC_MAX_OUTPUT_CHARS, STEAM_ID
from data_layer.snapshot import local_snapshot_dir

_RUNNER = Path(__file__).parent / "_sandbox_runner.py"
_PROJECT_ROOT = Path(__file__).parent.parent

# The ONLY parent env vars forwarded to the child. PATH + SYSTEMROOT are needed
# for the interpreter to start (Windows requires SystemRoot), TEMP/TMP for
# tempfile, LANG/LC_ALL for locale. Everything else — STEAM_API_KEY,
# LLM_API_KEY, SUPABASE_*, UPSTASH_*, LANGSMITH_* — is dropped on the floor.
_ENV_ALLOWLIST = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG", "LC_ALL")


def child_env() -> dict[str, str]:
    """Build the scrubbed environment for the sandbox child (see _ENV_ALLOWLIST).
    Exposed as a function so the sandbox tests can assert nothing secret leaks."""
    env = {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}
    env.update({
        "HUNDO_SANDBOX": "1",
        "HUNDO_EXEC_MEMORY_MB": str(EXEC_MEMORY_MB),
        "HUNDO_MAX_OUTPUT_CHARS": str(EXEC_MAX_OUTPUT_CHARS),
    })
    return env


def parse_output(stdout: str, stderr: str, returncode: Optional[int]) -> tuple[Optional[str], Optional[str]]:
    """Decode the runner's output. Protocol: the LAST non-empty stdout line is the
    JSON envelope {"result": ...} or {"error": ...}. Anything before it is noise
    (print() is captured in the runner, but a C-extension crash or interpreter
    warning can still write to stdout). Returns (result, error) — exactly one set."""
    lines = [ln for ln in (stdout or "").splitlines() if ln.strip()]
    stderr = (stderr or "").strip()

    if not lines:
        # No envelope means the child died before printing — most often the
        # memory cap (RLIMIT_AS) or a hard crash killed it.
        if returncode not in (0, None):
            hint = stderr or f"process exited with code {returncode} (possible memory-limit kill)"
            return None, f"Sandbox crashed: {hint}"
        return None, stderr or "Sandbox produced no output."

    last = lines[-1].strip()
    try:
        data = json.loads(last)
    except json.JSONDecodeError:
        return None, f"Sandbox output was not valid JSON: {last[:200]}"
    if not isinstance(data, dict):
        return None, f"Sandbox output was not a JSON object: {last[:200]}"
    if "error" in data:
        return None, str(data["error"])
    return str(data.get("result", "")), None


def run_user_code(
    code: str,
    frames: Optional[dict[str, pd.DataFrame]] = None,  # unused; runner loads by snapshot dir
    steam_id: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Execute `code` in an isolated subprocess against a user's frozen snapshot.

    The parent resolves WHOSE snapshot (steam_id, else the configured default)
    and hydrates it into the local cache; the child is handed that directory as
    its only argument and reads it with data_layer.frames — it never sees a
    Steam ID, a credential, or object storage.

    Returns (result_text, error_text) — exactly one is non-None.
    """
    sid = str(steam_id or STEAM_ID)
    snap_dir = local_snapshot_dir(sid).resolve()

    cmd = [sys.executable, "-I", "-B", "-X", "utf8", str(_RUNNER), str(snap_dir)]
    try:
        proc = subprocess.run(
            cmd,
            input=code,
            capture_output=True,
            text=True,
            encoding="utf-8",   # LLM code/output may contain non-cp1252 chars (→, é, …)
            errors="replace",
            timeout=EXEC_TIMEOUT_SECONDS,
            cwd=str(_PROJECT_ROOT),
            env=child_env(),
        )
    except subprocess.TimeoutExpired:
        return None, f"Execution timed out after {EXEC_TIMEOUT_SECONDS}s."

    return parse_output(proc.stdout, proc.stderr, proc.returncode)
