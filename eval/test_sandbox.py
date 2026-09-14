"""Sandbox containment checks (Steps 3 / 9.5 / 23.1). Offline; needs no LLM.
Run:  py eval/test_sandbox.py      (exit code 1 on any failure)

Covers the two probes from the 2026-09 review (file read + URL fetch through
pandas), the dunder/tofile AST gate, the env allowlist, print capture, and the
legitimate paths that must keep working (analysis, to_csv() → string).
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.sandbox import run_user_code, child_env, parse_output  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
_results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"\n      {detail}" if detail and not ok else ""))


def run(code: str):
    return run_user_code(code, {})


# ── legitimate paths keep working ─────────────────────────────────────────────
r, e = run("result = games.shape[0]")
check("analysis: known-good snippet returns a result", r is not None and e is None, f"r={r!r} e={e!r}")

r, e = run("result = games.head(2).to_csv()")
check("analysis: to_csv() to a STRING still works", r is not None and e is None, f"e={e!r}")

r, e = run("import json\nresult = json.dumps({'n': int(achievements.shape[0])})")
check("analysis: allowlisted import (json) works", r is not None and e is None, f"e={e!r}")

r, e = run("print('noise before the envelope')\nresult = 7")
check("output: print() is captured, result survives", r == "7" and e is None, f"r={r!r} e={e!r}")

r, e = run("print('only printed')")
check("output: print-only code gets the assign-to-result hint",
      e is not None and "print()" in e, f"e={e!r}")

r, e = run("result = games['nonexistent_col']")
check("error: runtime error is reported", e is not None, f"r={r!r}")

r, e = run("x = 1 + 1")
check("error: no result variable is reported", e is not None, f"r={r!r}")

# ── containment ───────────────────────────────────────────────────────────────
r, e = run("result = pd.read_csv('requirements.txt', sep='\\x01', header=None, engine='python').to_string()")
check("blocked: pandas file read (review probe #1)", e is not None and "disabled" in e, f"r={str(r)[:80]!r} e={e!r}")

r, e = run("result = pd.read_csv('https://example.com/', sep='\\x01', header=None, engine='python').shape")
check("blocked: pandas URL fetch (review probe #2)", e is not None and "disabled" in e, f"r={r!r} e={e!r}")

r, e = run("result = pd.io.parsers.readers.read_csv('requirements.txt').shape")
check("blocked: deep pandas reader path (bypasses top-level wrapper)", e is not None, f"r={r!r}")

r, e = run("result = np.loadtxt('requirements.txt', dtype=str).shape")
check("blocked: numpy file read", e is not None, f"r={r!r}")

probe = _ROOT / "sandbox_probe.csv"
probe.unlink(missing_ok=True)
r, e = run("games.to_csv('sandbox_probe.csv')\nresult = 'written'")
check("blocked: pandas file WRITE", e is not None and not probe.exists(), f"r={r!r} e={e!r} exists={probe.exists()}")
probe.unlink(missing_ok=True)

r, e = run("result = ().__class__.__base__.__subclasses__()")
check("blocked: dunder class-hierarchy escape (AST gate)",
      e is not None and "not allowed" in e and "__" in e, f"e={e!r}")

r, e = run("np.arange(3).tofile('probe.bin')\nresult = 1")
check("blocked: ndarray.tofile (C-level write, AST gate)", e is not None and "tofile" in e, f"e={e!r}")
(_ROOT / "probe.bin").unlink(missing_ok=True)

r, e = run("import os\nresult = os.getcwd()")
check("blocked: import os", e is not None and "not allowed" in e, f"e={e!r}")

r, e = run("import matplotlib\nresult = 1")
check("blocked: matplotlib no longer importable in the sandbox", e is not None, f"r={r!r}")

# ── env scrub ─────────────────────────────────────────────────────────────────
env = child_env()
secret_names = {"STEAM_API_KEY", "LLM_API_KEY", "TAVILY_API_KEY", "SUPABASE_URL",
                "SUPABASE_SERVICE_KEY", "UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN",
                "LANGSMITH_API_KEY"}
leaked = sorted(k for k in env if k in secret_names
                or k.endswith(("_KEY", "_TOKEN", "_SECRET", "_URL", "_PASSWORD")))
check("env: no secret-shaped variable reaches the child", not leaked, f"leaked={leaked}")
check("env: sandbox marker + limits present",
      env.get("HUNDO_SANDBOX") == "1" and "HUNDO_EXEC_MEMORY_MB" in env, f"env={sorted(env)}")
check("env: nothing beyond the allowlist + HUNDO_* is forwarded",
      all(k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG", "LC_ALL") or k.startswith("HUNDO_") for k in env),
      f"env={sorted(env)}")

# ── output protocol (pure function) ───────────────────────────────────────────
r, e = parse_output('junk line\n{"result": "42"}\n', "", 0)
check("protocol: last line wins over leading noise", r == "42" and e is None, f"r={r!r} e={e!r}")
r, e = parse_output("", "Killed", 137)
check("protocol: no envelope + nonzero exit = crash error", e is not None and "crashed" in e, f"e={e!r}")

# ── timeout ───────────────────────────────────────────────────────────────────
t0 = time.time()
r, e = run("import time\ntime.sleep(999)")
check("timeout: sleep(999) is killed", e is not None and "timed out" in e, f"e={e!r}")
print(f"      (timeout fired in {time.time() - t0:.1f}s)")

print()
passed, total = sum(_results), len(_results)
print(f"  {passed} passed, {total - passed} failed")
sys.exit(0 if passed == total else 1)
