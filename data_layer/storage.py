"""Pluggable blob storage: local filesystem (dev) or Supabase Storage (prod).

Backend is chosen by config at import time: if SUPABASE_URL + SUPABASE_SERVICE_KEY
are set (USE_SUPABASE_STORAGE), blobs go to Supabase Storage over its REST API;
otherwise everything stays on the local disk exactly as before, so the dev loop
and the eval are untouched. No new dependency — Supabase Storage is driven with
`requests` (already pinned).

Two logical buckets, referenced by short id:
    "charts"    -> generated PNGs (public-read; served straight to <img>)
    "snapshots" -> per-user frame JSON (private; fetched via the service key)

Only api/ and data_layer/ call this — the agent never touches storage.
"""
from datetime import datetime, timezone
from pathlib import Path
import time

import requests

from config import (
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY,
    SUPABASE_BUCKET_CHARTS,
    SUPABASE_BUCKET_SNAPSHOTS,
    USE_SUPABASE_STORAGE,
)

_TIMEOUT = 20  # seconds per Supabase REST call

# logical id -> (local dir, supabase bucket name)
_LOCAL_DIRS = {
    "charts": Path("data") / "charts",
    "snapshots": Path("data") / "snapshot",
}
_SB_BUCKETS = {
    "charts": SUPABASE_BUCKET_CHARTS,
    "snapshots": SUPABASE_BUCKET_SNAPSHOTS,
}


def using_supabase() -> bool:
    return USE_SUPABASE_STORAGE


# ── helpers ───────────────────────────────────────────────────────────────────

def _local_path(bucket: str, key: str) -> Path:
    return _LOCAL_DIRS[bucket] / key


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}


def _obj_url(bucket: str, key: str) -> str:
    return f"{SUPABASE_URL}/storage/v1/object/{_SB_BUCKETS[bucket]}/{key}"


def _iso_to_epoch(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


# ── core operations ─────────────────────────────────────────────────────────────

def put(bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    """Write bytes to `bucket/key`, overwriting any existing object."""
    if using_supabase():
        # x-upsert lets us overwrite without a separate exists() check.
        headers = {**_auth_headers(), "Content-Type": content_type, "x-upsert": "true"}
        r = requests.post(_obj_url(bucket, key), headers=headers, data=data, timeout=_TIMEOUT)
        r.raise_for_status()
        return
    p = _local_path(bucket, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def get(bucket: str, key: str) -> bytes:
    """Read bytes from `bucket/key`. Raises if missing."""
    if using_supabase():
        r = requests.get(_obj_url(bucket, key), headers=_auth_headers(), timeout=_TIMEOUT)
        r.raise_for_status()
        return r.content
    return _local_path(bucket, key).read_bytes()


def exists(bucket: str, key: str) -> bool:
    if using_supabase():
        # /object/info returns metadata for a single object (404 if absent).
        url = f"{SUPABASE_URL}/storage/v1/object/info/{_SB_BUCKETS[bucket]}/{key}"
        try:
            r = requests.get(url, headers=_auth_headers(), timeout=_TIMEOUT)
            return r.status_code == 200
        except requests.RequestException:
            return False
    return _local_path(bucket, key).exists()


def delete(bucket: str, key: str) -> None:
    if using_supabase():
        r = requests.delete(_obj_url(bucket, key), headers=_auth_headers(), timeout=_TIMEOUT)
        # 404 is fine — already gone.
        if r.status_code not in (200, 404):
            r.raise_for_status()
        return
    p = _local_path(bucket, key)
    if p.exists():
        p.unlink()


def updated_at(bucket: str, key: str) -> float | None:
    """Last-modified time as epoch seconds, or None if the object is missing."""
    if using_supabase():
        url = f"{SUPABASE_URL}/storage/v1/object/info/{_SB_BUCKETS[bucket]}/{key}"
        try:
            r = requests.get(url, headers=_auth_headers(), timeout=_TIMEOUT)
            if r.status_code != 200:
                return None
            info = r.json()
            return _iso_to_epoch(info.get("updated_at") or info.get("created_at"))
        except (requests.RequestException, ValueError):
            return None
    p = _local_path(bucket, key)
    return p.stat().st_mtime if p.exists() else None


def list_objects(bucket: str, prefix: str = "") -> list[dict]:
    """List objects under `prefix` as [{"key": str, "mtime": float}]. Best-effort:
    returns [] on error so cleanup never raises into a request."""
    if using_supabase():
        url = f"{SUPABASE_URL}/storage/v1/object/list/{_SB_BUCKETS[bucket]}"
        body = {
            "prefix": prefix,
            "limit": 1000,
            "offset": 0,
            "sortBy": {"column": "updated_at", "order": "desc"},
        }
        try:
            r = requests.post(url, headers=_auth_headers(), json=body, timeout=_TIMEOUT)
            r.raise_for_status()
            items = r.json()
        except (requests.RequestException, ValueError):
            return []
        out = []
        for it in items:
            name = it.get("name")
            if not name or it.get("id") is None:
                continue  # folders have id=None — skip
            key = f"{prefix}{name}" if prefix else name
            out.append({"key": key, "mtime": _iso_to_epoch(it.get("updated_at") or it.get("created_at"))})
        return out

    base = _LOCAL_DIRS[bucket] / prefix if prefix else _LOCAL_DIRS[bucket]
    if not base.exists():
        return []
    out = []
    for p in base.rglob("*"):
        if p.is_file():
            try:
                key = str(p.relative_to(_LOCAL_DIRS[bucket])).replace("\\", "/")
                out.append({"key": key, "mtime": p.stat().st_mtime})
            except OSError:
                pass
    return out


def public_url(bucket: str, key: str) -> str:
    """Browser-reachable URL for a (public) object. For charts on Supabase this is
    the bucket's public object URL; locally it's the path served by the API's
    /charts static mount."""
    if using_supabase():
        return f"{SUPABASE_URL}/storage/v1/object/public/{_SB_BUCKETS[bucket]}/{key}"
    if bucket == "charts":
        return f"/charts/{key}"
    return f"/{key}"


def sweep(bucket: str, ttl_seconds: float, max_files: int) -> int:
    """Delete objects older than ttl, then cap the bucket to the newest max_files.
    Works for both backends. Best-effort — returns the number removed, never raises."""
    objs = sorted(list_objects(bucket), key=lambda o: o["mtime"], reverse=True)  # newest first
    now = time.time()
    removed = 0
    for i, o in enumerate(objs):
        too_old = (now - o["mtime"]) > ttl_seconds
        over_cap = i >= max_files
        if too_old or over_cap:
            try:
                delete(bucket, o["key"])
                removed += 1
            except Exception:
                pass
    return removed
