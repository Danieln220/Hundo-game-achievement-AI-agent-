"""Subprocess entry point for sandboxed code execution. SECURITY-CRITICAL.

Spawned by sandbox.py as `python -I -B -X utf8 _sandbox_runner.py <snapshot_dir>`
under a SCRUBBED environment. Reads user code from stdin, executes it in a
locked-down namespace, and prints exactly one JSON line as the LAST line of
stdout: {"result": "..."} or {"error": "..."}.

Lockdown layers (in addition to the subprocess, timeout and env scrub in sandbox.py):
  1. Memory cap          — RLIMIT_AS on POSIX (the deploy target); no-op on Windows
  2. No secrets, no I/O deps — imports ONLY pandas/numpy/json + data_layer.frames
                            (dependency-free); never config, dotenv or storage
  3. AST gate            — any name/attribute starting with '__' is rejected before
                            exec (blocks the ().__class__.__base__.__subclasses__()
                            family); 'tofile'/'fromfile' rejected (C-level file I/O)
  4. Restricted importer — only data-analysis libs; os/subprocess/socket/... denied
  5. Curated builtins    — open/eval/exec/compile/__import__/getattr removed
  6. I/O chokepoints     — builtins.open replaced process-wide (every Python-level
                            open() in pandas/numpy internals fails); pandas
                            io.common path/URL resolution + urlopen blocked; numpy
                            file readers/writers blocked
  7. Output capture      — sys.stdout is swapped during exec so print() can't
                            corrupt the envelope; result string is truncated

ORDER MATTERS: load the snapshot FIRST (it needs real open()), then lock the
interpreter down right before exec(). Never import this module directly.
"""
import ast
import io
import json
import os
import sys
from pathlib import Path

# -I keeps the script dir off sys.path; add the project root so the
# dependency-free frames module is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import builtins as _builtins

import pandas as pd
import numpy as np

from data_layer.frames import frames_from_dir  # json + pandas only — no config/storage


def _emit(payload: dict) -> None:
    """Print the JSON envelope as the final stdout line and exit."""
    sys.stdout.flush()
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()
    sys.exit(0)


code = sys.stdin.read()

# Limits arrive via the scrubbed env (sandbox.child_env) — the runner never
# imports config, which would load .env and re-inject secrets.
_MEMORY_MB = int(os.environ.get("HUNDO_EXEC_MEMORY_MB", "2048"))
_MAX_OUTPUT_CHARS = int(os.environ.get("HUNDO_MAX_OUTPUT_CHARS", "20000"))


# ── Layer 1: memory cap (POSIX only) ──────────────────────────────────────────
try:
    import resource
    _bytes = _MEMORY_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (_bytes, _bytes))
except Exception:
    pass  # Windows / unsupported — rely on the timeout instead


# ── Layer 2: load the snapshot from the ONE directory the parent hydrated ──────
if len(sys.argv) < 2 or not sys.argv[1]:
    _emit({"error": "Sandbox misconfigured: no snapshot directory given."})
try:
    frames = frames_from_dir(sys.argv[1])
except Exception as exc:
    _emit({"error": f"Sandbox could not load the snapshot: {type(exc).__name__}: {exc}"})


# ── Layer 3: AST gate ─────────────────────────────────────────────────────────
# LLM pandas code never needs dunder attributes; rejecting them at parse time
# closes the class-hierarchy escapes that no builtins restriction can. tofile /
# fromfile are C-level file I/O on ndarray/numpy that the open() chokepoint
# below cannot intercept.
_BLOCKED_ATTRS = {"tofile", "fromfile"}


def _gate(src: str) -> ast.AST:
    tree = ast.parse(src, filename="<sandbox>")
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.Name):
            name = node.id
        if name and (name.startswith("__") or name in _BLOCKED_ATTRS):
            raise PermissionError(
                f"'{name}' is not allowed in the sandbox (dunder / file access)."
            )
    return tree


try:
    _tree = _gate(code)
except SyntaxError as exc:
    _emit({"error": f"SyntaxError: {exc}"})
except PermissionError as exc:
    _emit({"error": f"PermissionError: {exc}"})


# ── Layer 4: restricted importer (allowlist) ──────────────────────────────────
# Permit only data-analysis libraries (and their submodules). Everything else —
# os, sys, subprocess, socket, shutil, urllib, matplotlib (unused since charts
# went client-side in 19.2; savefig was a file-write path) — is denied. An
# allowlisted importer (vs. removing import entirely) preserves the lazy
# sub-imports pandas/numpy do internally.
_ALLOWED_ROOTS = {
    "pandas", "numpy",
    "datetime", "time", "math", "statistics", "decimal", "fractions",
    "itertools", "functools", "collections", "re", "json",
}
_real_import = _builtins.__import__


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".")[0]
    if root in _ALLOWED_ROOTS:
        return _real_import(name, globals, locals, fromlist, level)
    raise ImportError(f"Import of '{name}' is not allowed in the sandbox.")


# ── Layer 5: curated builtins ─────────────────────────────────────────────────
# A hand-picked safe set. Notably absent: open, eval, exec, compile, input,
# getattr/setattr/delattr, globals, vars, dir, and the real __import__.
_SAFE_BUILTIN_NAMES = [
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
    "float", "format", "frozenset", "int", "isinstance", "issubclass", "len",
    "list", "map", "max", "min", "next", "range", "repr", "reversed",
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

# print() inside the sandbox is CAPTURED, not forwarded: stdout is the result
# channel. Kept so code that prints doesn't crash; the text is discarded.
_printed: list[str] = []


def _capture_print(*args, **kwargs):
    _printed.append(" ".join(str(a) for a in args))


_safe_builtins["print"] = _capture_print


# ── Layer 6: file / URL chokepoints (process-wide, child only) ────────────────
_IO_MSG = "File and network access is disabled in the sandbox."


def _blocked(*_a, **_k):
    raise PermissionError(_IO_MSG)


# (a) Every Python-level open() anywhere in the process — pandas readers/writers,
#     numpy npyio (load/save/loadtxt/genfromtxt/savetxt), json.load(open(...)).
_builtins.open = _blocked

# (b) pandas' path/URL resolution + its urlopen wrapper. get_handle() resolves
#     _get_filepath_or_buffer from io.common's globals at CALL time, so patching
#     the module attribute intercepts every reader, even ones that bound
#     get_handle by name at import. In-memory buffers (StringIO/BytesIO) pass, so
#     df.to_csv() → string keeps working; any str/bytes/PathLike path fails.
try:
    import pandas.io.common as _pdc
    _real_resolve = _pdc._get_filepath_or_buffer

    def _guarded_resolve(filepath_or_buffer, *a, **k):
        if isinstance(filepath_or_buffer, (str, bytes, os.PathLike)):
            raise PermissionError(_IO_MSG)
        return _real_resolve(filepath_or_buffer, *a, **k)

    _pdc._get_filepath_or_buffer = _guarded_resolve
    _pdc.urlopen = _blocked
except Exception:
    pass  # the open() chokepoint above still applies

# (c) Belt and braces on the public entry points the LLM would actually type.
for _name in (
    "read_csv", "read_table", "read_fwf", "read_json", "read_html", "read_xml",
    "read_excel", "read_pickle", "read_parquet", "read_feather", "read_orc",
    "read_sas", "read_spss", "read_stata", "read_hdf", "read_sql", "read_sql_table",
    "read_sql_query", "read_clipboard",
):
    if hasattr(pd, _name):
        setattr(pd, _name, _blocked)
for _mod in (np, getattr(np, "lib", None), getattr(getattr(np, "lib", None), "npyio", None),
             getattr(getattr(np, "lib", None), "_npyio_impl", None)):
    if _mod is None:
        continue
    for _name in ("load", "save", "savez", "savez_compressed", "loadtxt", "genfromtxt",
                  "savetxt", "fromregex", "fromfile", "memmap"):
        if hasattr(_mod, _name):
            try:
                setattr(_mod, _name, _blocked)
            except Exception:
                pass


# ── Layer 7: execute with stdout captured ─────────────────────────────────────
_ns = {
    "__builtins__": _safe_builtins,
    "__name__": "__sandbox__",
    "pd": pd,
    "np": np,
    "json": json,
    "games":          frames["games"],
    "achievements":   frames["achievements"],
    "player_unlocks": frames["player_unlocks"],
}

_real_stdout = sys.stdout
sys.stdout = io.StringIO()  # anything a library prints during exec is swallowed
try:
    exec(compile(_tree, "<sandbox>", "exec"), _ns)  # noqa: S102
    result = _ns.get("result", None)
    sys.stdout = _real_stdout
    if result is None:
        hint = " (print() output is not returned — assign the answer to `result`)" if _printed else ""
        _emit({"error": f"No `result` variable set. Assign your answer to `result`.{hint}"})
    text = str(result)
    if len(text) > _MAX_OUTPUT_CHARS:
        text = text[:_MAX_OUTPUT_CHARS] + "... [truncated]"
    _emit({"result": text})
except SystemExit:
    raise
except BaseException as exc:  # noqa: BLE001 — anything else becomes an error envelope
    sys.stdout = _real_stdout
    _emit({"error": f"{type(exc).__name__}: {exc}"})
