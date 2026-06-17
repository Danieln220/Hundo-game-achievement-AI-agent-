"""Subprocess entry point for sandboxed code execution. SECURITY-CRITICAL.

Spawned by sandbox.py as a child process. Reads user code from stdin, executes
it in a LOCKED-DOWN namespace, and prints exactly one JSON line to stdout:
{"result": "..."} or {"error": "..."}.

Lockdown layers (in addition to the subprocess + timeout enforced by sandbox.py):
  1. Memory cap         — RLIMIT_AS on POSIX (the deploy target); no-op on Windows
  2. Restricted import  — only data-analysis libs may be imported; os/subprocess/
                          socket/etc. are denied
  3. Curated builtins   — open/eval/exec/compile/__import__ removed; only safe
                          names exposed
  4. Output truncation  — result string is capped so a huge dump can't flood the UI

ORDER MATTERS: we do every legitimate import and load the snapshot FIRST, then
lock the interpreter down right before exec(). Never import this directly.
"""
import sys
import json
from pathlib import Path

# Ensure the project root is on sys.path so data_layer is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

import builtins as _builtins

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")  # non-interactive backend — no display needed
import matplotlib.pyplot as plt

# Load the frozen snapshot — fast, just reads cached JSON
from data_layer.snapshot import load_frames
from config import EXEC_MEMORY_MB, EXEC_MAX_OUTPUT_CHARS


# ── Layer 1: memory cap (POSIX only) ──────────────────────────────────────────
# resource is Unix-only. Our prod target (Render) is Linux, so the cap applies
# there. On Windows dev the hard timeout in sandbox.py is the backstop.
try:
    import resource
    _bytes = EXEC_MEMORY_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (_bytes, _bytes))
except Exception:
    pass  # Windows / unsupported — rely on the timeout instead


# ── argv[1] (optional) = SteamID64 telling us whose snapshot to load ──────────
_steam_id = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
frames = load_frames(_steam_id) if _steam_id else load_frames()


# ── Layer 2: restricted importer (allowlist) ──────────────────────────────────
# Permit only data-analysis libraries (and their submodules). Everything else —
# os, sys, subprocess, socket, shutil, pathlib writes, etc. — is denied. An
# allowlisted importer (vs. removing import entirely) preserves the lazy
# sub-imports pandas/matplotlib do internally.
_ALLOWED_ROOTS = {
    "pandas", "numpy", "matplotlib", "mpl_toolkits",
    "datetime", "math", "statistics", "decimal", "fractions",
    "itertools", "functools", "collections", "re", "json",
}
_real_import = _builtins.__import__


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".")[0]
    if root in _ALLOWED_ROOTS:
        return _real_import(name, globals, locals, fromlist, level)
    raise ImportError(f"Import of '{name}' is not allowed in the sandbox.")


# ── Layer 3: curated builtins ─────────────────────────────────────────────────
# A hand-picked safe set. Notably absent: open, eval, exec, compile, input,
# globals, vars, and the real __import__ (replaced by _safe_import).
_SAFE_BUILTIN_NAMES = [
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
    "float", "format", "frozenset", "int", "isinstance", "issubclass", "len",
    "list", "map", "max", "min", "next", "print", "range", "repr", "reversed",
    "round", "set", "slice", "sorted", "str", "sum", "tuple", "type", "zip",
    "True", "False", "None",
    # exceptions user code may legitimately catch/raise
    "Exception", "ValueError", "KeyError", "IndexError", "TypeError",
    "ZeroDivisionError", "AttributeError", "StopIteration",
]
_safe_builtins = {
    n: getattr(_builtins, n) for n in _SAFE_BUILTIN_NAMES if hasattr(_builtins, n)
}
_safe_builtins["__import__"] = _safe_import

_ns = {
    "__builtins__": _safe_builtins,
    "pd": pd,
    "np": np,
    "plt": plt,
    "matplotlib": matplotlib,
    "games":          frames["games"],
    "achievements":   frames["achievements"],
    "player_unlocks": frames["player_unlocks"],
}

code = sys.stdin.read()

try:
    exec(code, _ns)  # noqa: S102
    result = _ns.get("result", None)
    if result is None:
        print(json.dumps({"error": "No `result` variable set. Assign your answer to `result`."}))
    else:
        # ── Layer 4: output truncation ────────────────────────────────────────
        text = str(result)
        if len(text) > EXEC_MAX_OUTPUT_CHARS:
            text = text[:EXEC_MAX_OUTPUT_CHARS] + "... [truncated]"
        print(json.dumps({"result": text}))
except Exception as exc:
    print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
