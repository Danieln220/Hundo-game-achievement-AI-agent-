"""Quick smoke-tests for the sandbox. Run with: python -m eval.test_sandbox"""
import time
from agent.sandbox import run_user_code

def test(label, code, expect_result=False, expect_error=False):
    r, e = run_user_code(code, {})
    ok = (expect_result and r is not None) or (expect_error and e is not None)
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}")
    print(f"       result={r!r}  error={e!r}")
    return ok

print("=== Sandbox tests ===")
t1 = test("known-good snippet",  "result = games.shape[0]",             expect_result=True)
t2 = test("runtime error",       "result = games['nonexistent_col']",   expect_error=True)
t3 = test("no result variable",  "x = 1 + 1",                           expect_error=True)

t0 = time.time()
t4 = test("timeout (sleep 999)", "import time\ntime.sleep(999)",        expect_error=True)
print(f"       timeout fired in {round(time.time()-t0, 1)}s")

all_pass = all([t1, t2, t3, t4])
print()
print("=== Result:", "ALL PASS" if all_pass else "SOME FAILED", "===")
