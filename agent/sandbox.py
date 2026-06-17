"""Sandboxed execution of model-written pandas code. SECURITY-CRITICAL.

This runs LLM-generated code. Isolation layers:
  1. Separate subprocess — a crash or hang can't take down the main process
  2. Hard timeout — EXEC_TIMEOUT_SECONDS kills the subprocess if it stalls
  3. Network blocking — common network modules set to None before exec()
  4. Restricted namespace — only pd, np, and the three DataFrames are exposed

Note: network blocking is sys.modules-level, not OS-level. It stops naive
imports but is not a kernel-enforced sandbox. Acceptable for this use case.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

from config import EXEC_TIMEOUT_SECONDS

_RUNNER = Path(__file__).parent / "_sandbox_runner.py"
_PROJECT_ROOT = Path(__file__).parent.parent

# Force the child into Python UTF-8 mode so stdin/stdout encoding matches the
# parent (LLM code/output can contain non-cp1252 chars on Windows).
_CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def run_user_code(
    code: str,
    frames: Optional[dict[str, pd.DataFrame]] = None,  # unused; runner loads by steam_id
    steam_id: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Execute `code` in an isolated subprocess against a user's frozen snapshot.

    The runner loads the snapshot itself (keeps the subprocess boundary clean),
    so `steam_id` is forwarded to tell it WHOSE data to load. Passing None falls
    back to the default STEAM_ID inside the runner.

    Returns (result_text, error_text) — exactly one is non-None.
    """
    cmd = [sys.executable, str(_RUNNER)]
    if steam_id:
        cmd.append(str(steam_id))
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
            env=_CHILD_ENV,
        )
    except subprocess.TimeoutExpired:
        return None, f"Execution timed out after {EXEC_TIMEOUT_SECONDS}s."

    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()

    if not stdout:
        # No JSON line means the child died before printing — most often the
        # memory cap (RLIMIT_AS) or a hard crash killed it.
        if proc.returncode not in (0, None):
            hint = stderr or f"process exited with code {proc.returncode} (possible memory-limit kill)"
            return None, f"Sandbox crashed: {hint}"
        return None, stderr or "Sandbox produced no output."

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None, f"Sandbox output was not valid JSON: {stdout[:200]}"

    if "error" in data:
        return None, data["error"]
    return data["result"], None
