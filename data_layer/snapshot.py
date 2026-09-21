"""Fetch a user's Steam data, cache to JSON, and load it into DataFrames.

Multi-user: each user's snapshot lives in its own dir, keyed by SteamID64:
    data/snapshot/<steam_id>/owned_games.json
    data/snapshot/<steam_id>/schemas.json       {appid: GetSchemaForGame}
    data/snapshot/<steam_id>/achievements.json  {appid: GetPlayerAchievements}
    data/snapshot/<steam_id>/global_pct.json    {appid: GetGlobalAchievementPercentages}

Build against a frozen snapshot: reproducible runs, stable eval, no API hammering.
A legacy flat layout (files directly under data/snapshot/) is still honoured for
the default STEAM_ID so the existing eval keeps working untouched.
"""
import json
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from config import SNAPSHOT_DIR, STEAM_ID, SNAPSHOT_LOCK_TTL, SNAPSHOT_WAIT_MAX
from . import steam_client
from . import storage
from . import cache
from .frames import frames_from_dir

# Bounded so we fetch fast without tripping Steam's rate limits. Each game needs
# 3 calls, so a big library = hundreds of calls; more workers = shorter wall time.
_FETCH_WORKERS = 16

# The four files that make up one user's snapshot. data/snapshot/<id>/ is the
# local cache; in Supabase mode it also mirrors to the "snapshots" bucket under
# the same <id>/<file> key. The local dir is just a regenerable cache then.
# owned_games.json is LAST — it's the readiness marker (has_snapshot checks it),
# so it must be written/uploaded only after the per-game files exist (no
# half-built snapshot ever looks "ready" to a concurrent reader).
_SNAP_FILES = ("schemas.json", "achievements.json", "global_pct.json", "meta.json",
               "owned_games.json")

# A build that lost more than this share of its per-game calls (after a retry
# pass) is INCOMPLETE — we fail it instead of freezing the gaps into a snapshot
# that then looks "ready" for SNAPSHOT_TTL_DAYS (23.3a). Below the threshold the
# few failures are recorded in meta.json and re-fetched on the next build.
_MAX_FAILED_SHARE = 0.10


def _snap_key(steam_id: str, fname: str) -> str:
    return f"{steam_id}/{fname}"


def _upload_snapshot(steam_id: str) -> None:
    """Mirror the locally-built snapshot up to object storage (Supabase mode)."""
    out = _user_dir(steam_id)
    for f in _SNAP_FILES:
        p = out / f
        if p.exists():
            storage.put("snapshots", _snap_key(steam_id, f), p.read_bytes(),
                        content_type="application/json")


def _ensure_local_cache(steam_id: str) -> None:
    """In Supabase mode, hydrate the local cache dir from the bucket if it's empty
    (e.g. a fresh container). No-op locally or when nothing is stored remotely."""
    if not storage.using_supabase():
        return
    out = _user_dir(steam_id)
    if (out / "owned_games.json").exists():
        return  # already cached on this instance
    if not storage.exists("snapshots", _snap_key(steam_id, "owned_games.json")):
        return  # nothing stored remotely yet
    out.mkdir(parents=True, exist_ok=True)
    for f in _SNAP_FILES:
        key = _snap_key(steam_id, f)
        if storage.exists("snapshots", key):
            (out / f).write_bytes(storage.get("snapshots", key))

# A snapshot older than this is considered stale and rebuilt by ensure_snapshot
# when a max_age is requested. Keeps cached data fresh without piling up forever.
DEFAULT_MAX_AGE_DAYS = 7


def load_schemas(steam_id: str = STEAM_ID) -> dict:
    """Raw GetSchemaForGame payloads keyed by appid (str). Used by the library view
    for achievement icons — NOT part of the agent's fixed 3-frame contract, so it
    lives here as a separate read. Hydrates the local cache first (Supabase mode)."""
    _ensure_local_cache(str(steam_id))
    snap = _resolve_snapshot_dir(str(steam_id))
    p = snap / "schemas.json"
    return json.loads(p.read_text()) if p.exists() else {}


PRIVATE_PROFILE_MSG = (
    "This Steam profile is private. Set 'Game details' to Public in "
    "Steam → Profile → Privacy Settings, then try again."
)


class PrivateProfileError(RuntimeError):
    """Raised when a profile is private / friends-only and exposes no games."""


def is_private_owned_payload(owned: dict) -> bool:
    """True if a GetOwnedGames payload is the empty-response shape Steam returns
    for private / friends-only profiles (no 'games' key at all). Used by the API
    to reject a private profile UP-FRONT instead of starting a build."""
    return owned.get("response", {}).get("games") is None


def _user_dir(steam_id: str) -> Path:
    return Path(SNAPSHOT_DIR) / str(steam_id)


def _resolve_snapshot_dir(steam_id: str) -> Path:
    """Where THIS user's snapshot lives. Prefer the per-user dir; fall back to
    the legacy flat layout for the configured default user."""
    user_dir = _user_dir(steam_id)
    if user_dir.exists():
        return user_dir
    base = Path(SNAPSHOT_DIR)
    if str(steam_id) == str(STEAM_ID) and (base / "owned_games.json").exists():
        return base  # legacy flat snapshot
    return user_dir  # may not exist yet → load_frames returns empty frames


def has_snapshot(steam_id: str = STEAM_ID) -> bool:
    """True if a usable snapshot already exists for this user (local cache or, in
    Supabase mode, the object store)."""
    if (_resolve_snapshot_dir(steam_id) / "owned_games.json").exists():
        return True
    if storage.using_supabase():
        return storage.exists("snapshots", _snap_key(str(steam_id), "owned_games.json"))
    return False


def snapshot_version(steam_id: str = STEAM_ID) -> str | None:
    """Cheap identity of the CURRENT snapshot build (local marker mtime) — used to
    key the answer cache (19.4) so a rebuild invalidates it naturally. Local-only
    on purpose: no network on the /ask hot path. Before the local cache is
    hydrated (first request on a fresh instance) it returns None → caller just
    skips caching for that one request."""
    marker = _resolve_snapshot_dir(steam_id) / "owned_games.json"
    try:
        return str(int(marker.stat().st_mtime))
    except OSError:
        return None


def snapshot_meta(steam_id: str = STEAM_ID) -> dict:
    """Build provenance for this user's snapshot (23.3a/b): {built_at, games,
    calls, failed}. Empty for snapshots built before meta.json existed — callers
    fall back to snapshot_age_days()."""
    p = _resolve_snapshot_dir(steam_id) / "meta.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def snapshot_age_days(steam_id: str = STEAM_ID) -> float | None:
    """Age of this user's snapshot in days, or None if there is none."""
    marker = _resolve_snapshot_dir(steam_id) / "owned_games.json"
    if marker.exists():
        return (time.time() - marker.stat().st_mtime) / 86400
    if storage.using_supabase():
        ts = storage.updated_at("snapshots", _snap_key(str(steam_id), "owned_games.json"))
        if ts:
            return (time.time() - ts) / 86400
    return None


def clear_snapshot(steam_id: str = STEAM_ID) -> bool:
    """Delete a user's snapshot (local cache + object store). Returns True if
    something was removed. Never touches the legacy flat layout (which holds the
    default user's eval data)."""
    removed = False
    user_dir = _user_dir(steam_id)
    if user_dir.exists():
        shutil.rmtree(user_dir)
        removed = True
    if storage.using_supabase():
        for f in _SNAP_FILES:
            if storage.exists("snapshots", _snap_key(str(steam_id), f)):
                try:
                    storage.delete("snapshots", _snap_key(str(steam_id), f))
                    removed = True
                except Exception:
                    pass
    return removed


class LockLostError(RuntimeError):
    """This build's lock was taken over by another build — stop and let it win."""


class SnapshotIncompleteError(RuntimeError):
    """Too many per-game Steam calls failed — the snapshot would have silent gaps."""


def _fetch_one(kind: str, steam_id: str, appid: int) -> tuple[str, int, dict, bool]:
    """Fetch a single endpoint for one game. Tagged by `kind` so results can be
    regrouped after running through the thread pool. The 4th element is `ok`:
    False means the CALL FAILED (vs. a legitimately empty payload, e.g. a game
    with no achievements) — the caller counts those instead of silently keeping
    the gap (23.3a)."""
    try:
        if kind == "schema":
            return kind, appid, steam_client.get_schema_for_game(appid), True
        if kind == "ach":
            return kind, appid, steam_client.get_player_achievements(steam_id, appid), True
        if kind == "pct":
            return kind, appid, steam_client.get_global_achievement_pct(appid), True
    except Exception as exc:
        print(f"  [fetch] {kind} {appid} failed: {type(exc).__name__}: {str(exc)[:120]}")
    return kind, appid, {}, False


def build_snapshot(steam_id: str = STEAM_ID, progress_cb=None, owned: dict | None = None) -> None:
    """Fetch one user's Steam data concurrently and write 4 JSON files into
    data/snapshot/<steam_id>/. Raises PrivateProfileError if the profile is private.

    `progress_cb(done, total)` is called as calls complete, so a UI can show a
    live progress bar (total = number of games * 3 endpoints).
    `owned` = an already-fetched GetOwnedGames payload (the API's up-front
    privacy check) so the build doesn't repeat that call."""
    steam_id = str(steam_id)
    out = _user_dir(steam_id)
    out.mkdir(parents=True, exist_ok=True)

    if owned is None:
        print(f"Fetching owned games for {steam_id}...")
        owned = steam_client.get_owned_games(steam_id)
    games = owned.get("response", {}).get("games")

    # A private/friends-only profile returns an empty response with no "games" key.
    if games is None:
        raise PrivateProfileError(PRIVATE_PROFILE_MSG)

    print(f"Found {len(games)} games. Fetching per-game data ({_FETCH_WORKERS} workers)...")

    # Flatten every (game, endpoint) into an independent unit so all calls — not
    # just one per game — share the worker pool. Big libraries finish far sooner.
    appids = [g["appid"] for g in games]
    schemas      = {a: {} for a in appids}
    achievements = {a: {} for a in appids}
    global_pct   = {a: {} for a in appids}
    buckets = {"schema": schemas, "ach": achievements, "pct": global_pct}

    work = [(kind, a) for a in appids for kind in ("schema", "ach", "pct")]
    total = len(work)
    done = 0

    failed: list[tuple[str, int]] = []
    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
        futures = [pool.submit(_fetch_one, kind, steam_id, a) for kind, a in work]
        for fut in as_completed(futures):
            kind, appid, data, ok = fut.result()
            buckets[kind][appid] = data
            if not ok:
                failed.append((kind, appid))
            done += 1
            if progress_cb:
                progress_cb(done, total)
            if done % 30 == 0 or done == total:
                print(f"  {done}/{total} calls done")

    # One retry pass over the failures (Steam 429s/5xx cluster in bursts; the
    # session already backed off, so a second try minutes later usually lands).
    if failed:
        print(f"  retrying {len(failed)} failed calls...")
        retry_work, failed = failed, []
        with ThreadPoolExecutor(max_workers=max(2, _FETCH_WORKERS // 4)) as pool:
            for kind, appid, data, ok in pool.map(
                    lambda ka: _fetch_one(ka[0], steam_id, ka[1]), retry_work):
                if ok:
                    buckets[kind][appid] = data
                else:
                    failed.append((kind, appid))
                if progress_cb:
                    progress_cb(done, total)   # heartbeat the build lock

    # Fail LOUDLY rather than store a snapshot with silent holes: a user whose
    # build half-failed would otherwise see wrong totals for SNAPSHOT_TTL_DAYS.
    if total and len(failed) / total > _MAX_FAILED_SHARE:
        raise SnapshotIncompleteError(
            f"{len(failed)} of {total} Steam calls failed ({len(failed) / total:.0%}) — "
            "Steam may be rate-limiting or down. Please try again in a few minutes."
        )
    if failed:
        print(f"  {len(failed)} calls still failing — recorded for the next build")

    (out / "schemas.json").write_text(json.dumps(schemas, indent=2))
    (out / "achievements.json").write_text(json.dumps(achievements, indent=2))
    (out / "global_pct.json").write_text(json.dumps(global_pct, indent=2))
    # Build provenance: which calls are still missing (re-fetched next build) and
    # how complete this snapshot is. Read by meta() / the /session "last updated".
    (out / "meta.json").write_text(json.dumps({
        "built_at": time.time(),
        "games": len(games),
        "calls": total,
        "failed": [[k, a] for k, a in failed],
    }, indent=2))
    # owned_games.json is written LAST and is the readiness marker (has_snapshot
    # checks it). Writing it only after the per-game files exist prevents a
    # concurrent reader (e.g. /session/status polling) from seeing a half-built
    # snapshot as "ready".
    (out / "owned_games.json").write_text(json.dumps(owned, indent=2))

    if storage.using_supabase():
        _upload_snapshot(steam_id)
        print(f"Snapshot mirrored to object storage ({storage._SB_BUCKETS['snapshots']}/{steam_id}/)")

    print(f"Snapshot complete — {len(games)} games, {len(_SNAP_FILES)} files in {out}/")


def _build_with_lock(steam_id: str, progress_cb=None, owned: dict | None = None) -> None:
    """Build a snapshot under a distributed lock so two concurrent first-time
    visitors (or a retry) don't both fetch the whole library at once. If another
    worker holds the lock, wait for it to finish and reuse its result; only build
    ourselves if that build vanished (lock expired / failed)."""
    lock_key = f"lock:snap:{steam_id}"

    def _build_holding_lock(token: str) -> None:
        # Heartbeat the (short-TTL) lock on every progress tick so a LIVE build
        # keeps it, but a build that dies mid-flight lets it expire within
        # SNAPSHOT_LOCK_TTL — so a waiter/new request can take over quickly.
        # The heartbeat is OWNED (23.3c): if this build lost the lock (it stalled
        # past the TTL and another build took over) we stop instead of racing it
        # — two builds writing the same files is how half-built snapshots happen.
        lost = False

        def hb(done: int, total: int) -> None:
            nonlocal lost
            if not lost and not cache.refresh_lock(lock_key, SNAPSHOT_LOCK_TTL, token):
                lost = True
                raise LockLostError(
                    "Another build took over this snapshot (this one stalled past "
                    f"{SNAPSHOT_LOCK_TTL}s)."
                )
            if progress_cb:
                progress_cb(done, total)
        try:
            build_snapshot(steam_id, progress_cb=hb, owned=owned)
        finally:
            cache.release_lock(lock_key, token)   # only if we still own it

    token = cache.acquire_lock(lock_key, ttl_seconds=SNAPSHOT_LOCK_TTL)
    if token:
        _build_holding_lock(token)
        return

    # Someone else is building — wait until their (heartbeated) lock clears, then
    # reuse it. has_snapshot alone isn't enough mid-build, so we wait on the LOCK.
    # If the holder dies, its lock expires (no heartbeat) and we take over.
    waited = 0
    while waited < SNAPSHOT_WAIT_MAX and cache.exists(lock_key):
        time.sleep(2)
        waited += 2
    if not has_snapshot(steam_id):
        token = cache.acquire_lock(lock_key, ttl_seconds=SNAPSHOT_LOCK_TTL)
        if token:
            _build_holding_lock(token)


def ensure_snapshot(
    steam_id: str = STEAM_ID,
    max_age_days: float | None = None,
    progress_cb=None,
    owned: dict | None = None,
    force: bool = False,
) -> None:
    """Build the snapshot if this user has none, or if `max_age_days` is given and
    the existing one is older than that. Otherwise reuse the cached snapshot.
    Builds run under a lock (see _build_with_lock) to avoid duplicate fetches.
    `owned` = a pre-fetched GetOwnedGames payload to reuse (see build_snapshot).
    `force` rebuilds even a fresh snapshot (the /session/refresh path)."""
    need_build = force or not has_snapshot(steam_id)
    if not need_build and max_age_days is not None:
        age = snapshot_age_days(steam_id)
        if age is not None and age > max_age_days:
            print(f"Snapshot is {age:.1f} days old (> {max_age_days}) — refreshing...")
            need_build = True
    if need_build:
        _build_with_lock(steam_id, progress_cb=progress_cb, owned=owned)


def local_snapshot_dir(steam_id: str = STEAM_ID) -> Path:
    """Hydrate this user's snapshot into the local cache (Supabase mode) and return
    the directory that holds it. This is the ONLY place the sandbox parent needs
    before spawning: the child (agent/_sandbox_runner.py) reads that directory via
    data_layer.frames and never touches object storage or config itself (23.1)."""
    _ensure_local_cache(str(steam_id))
    return _resolve_snapshot_dir(str(steam_id))


# One parsed copy of each user's frames per process, invalidated by the snapshot's
# own mtime (23.6d/22.3). The JSON was previously re-parsed on EVERY request (and
# again inside every sandbox spawn) — pure waste on a 0.1-CPU box. Frames are
# treated as READ-ONLY by the agent; a rebuild bumps the marker and evicts.
_FRAMES_CACHE: dict[str, tuple[str, dict]] = {}
_FRAMES_LOCK = threading.Lock()
_FRAMES_CACHE_MAX = 8      # small — this is a latency cache, not a store


def load_frames(steam_id: str = STEAM_ID) -> dict[str, pd.DataFrame]:
    """Load this user's cached snapshot into the three frames the agent expects:
        games          -> appid, name, playtime
        achievements   -> appid, api_name, display_name, description, rarity_pct, hidden
        player_unlocks -> appid, api_name, achieved, unlock_time
    Returns empty (correctly-typed) frames if no snapshot exists for the user.
    The JSON→frames transform itself lives in data_layer.frames (dependency-free,
    shared with the sandbox runner). Parsed frames are cached in-process and
    invalidated when the snapshot is rebuilt (see snapshot_version)."""
    snap_dir = local_snapshot_dir(steam_id)
    version = snapshot_version(steam_id) or ""
    key = str(steam_id)
    if version:
        with _FRAMES_LOCK:
            hit = _FRAMES_CACHE.get(key)
            if hit and hit[0] == version:
                return hit[1]

    frames = frames_from_dir(snap_dir)
    if version:
        with _FRAMES_LOCK:
            if len(_FRAMES_CACHE) >= _FRAMES_CACHE_MAX:
                _FRAMES_CACHE.pop(next(iter(_FRAMES_CACHE)), None)
            _FRAMES_CACHE[key] = (version, frames)
    return frames


if __name__ == "__main__":
    build_snapshot()
