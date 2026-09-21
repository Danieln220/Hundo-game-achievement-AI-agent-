"""Build-integrity checks for 23.3a: a snapshot whose Steam calls partly failed
must NOT be stored as ready. Fully offline — Steam + object storage are stubbed.
Run: PYTHONPATH=. python eval/snapshot_build_check.py"""
import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_layer import snapshot as S

_results = []
def check(label, ok, detail=""):
    _results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"\n      {detail}" if detail and not ok else ""))

SID = "76561197960287930"
OWNED = {"response": {"games": [{"appid": i, "name": f"Game {i}"} for i in range(10)]}}


class FakeSteam:
    """fail_for = {(kind, appid)} that raise; `heal_on_retry` makes them succeed
    the second time they're called (models a transient 429 the retry pass fixes)."""
    def __init__(self, fail_for=(), heal_on_retry=False):
        self.fail_for, self.heal_on_retry = set(fail_for), heal_on_retry
        self.seen = set()
    def _maybe_fail(self, kind, appid):
        key = (kind, appid)
        if key in self.fail_for:
            if self.heal_on_retry and key in self.seen:
                return
            self.seen.add(key)
            raise RuntimeError("simulated Steam 429")
    def get_owned_games(self, sid): return OWNED
    def get_schema_for_game(self, appid):
        self._maybe_fail("schema", appid); return {"game": {"availableGameStats": {"achievements": []}}}
    def get_player_achievements(self, sid, appid):
        self._maybe_fail("ach", appid); return {"playerstats": {"achievements": []}}
    def get_global_achievement_pct(self, appid):
        self._maybe_fail("pct", appid); return {"achievementpercentages": {"achievements": []}}


class NoStorage:
    def using_supabase(self): return False


def run_build(fake):
    """Build into a throwaway SNAPSHOT_DIR. Returns (error_or_None, snapshot_dir)."""
    tmp = Path(tempfile.mkdtemp())
    real_sd, real_client, real_storage = S.SNAPSHOT_DIR, S.steam_client, S.storage
    S.SNAPSHOT_DIR, S.steam_client, S.storage = str(tmp), fake, NoStorage()
    try:
        S.build_snapshot(SID)
        return None, tmp / SID
    except Exception as e:
        return e, tmp / SID
    finally:
        S.SNAPSHOT_DIR, S.steam_client, S.storage = real_sd, real_client, real_storage


# 1. clean build
err, d = run_build(FakeSteam())
check("clean build succeeds", err is None, repr(err))
check("clean build writes the readiness marker LAST", (d / "owned_games.json").exists())
meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
check("meta.json records no failures", meta.get("failed") == [] and meta.get("calls") == 30, meta)

# 2. transient failures that heal on the retry pass → complete snapshot
err, d = run_build(FakeSteam(fail_for={("schema", 1), ("pct", 2), ("ach", 3)}, heal_on_retry=True))
meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
check("transient failures are retried and recovered", err is None and meta.get("failed") == [],
      f"{err!r} {meta.get('failed')!r}")

# 3. a few permanent failures (<=10%) → build completes, failures RECORDED
err, d = run_build(FakeSteam(fail_for={("schema", 4), ("pct", 5)}))
meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
check("small failure rate still builds", err is None, repr(err))
check("…and records the failed calls in meta.json",
      sorted(map(tuple, meta.get("failed", []))) == [("pct", 5), ("schema", 4)], meta.get("failed"))

# 4. too many permanent failures (>10%) → LOUD failure, no ready snapshot
bad = {(k, a) for a in range(4) for k in ("schema", "ach", "pct")}   # 12/30 = 40%
err, d = run_build(FakeSteam(fail_for=bad))
check("high failure rate raises SnapshotIncompleteError", isinstance(err, S.SnapshotIncompleteError), repr(err))
check("…and NO readiness marker is written (snapshot never looks ready)",
      not (d / "owned_games.json").exists())
check("…and the message tells the user to retry", "try again" in str(err).lower(), str(err))

print()
p, t = sum(_results), len(_results)
print(f"  {p} passed, {t - p} failed")
sys.exit(0 if p == t else 1)
