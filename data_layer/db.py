"""Lean persistence over Supabase Postgres via its PostgREST API (Step 15.5).

Three tables, NO auth/accounts — the "user" is just the resolved SteamID64:
    steam_user  — identity (vanity, persona, avatar, first/last seen)
    snapshot    — metadata only (status, built_at, header stats, blob location)
    query_log   — usage + latency per /ask

Reuses the same Supabase project + service key as storage (no DB driver / no new
dependency — just `requests`). Every write is BEST-EFFORT: a failure (table
missing, network, FK) is swallowed so analytics/identity can never break a user's
answer. No-op entirely when USE_DB is false, so local dev + eval are untouched.

Create the tables once via the Supabase SQL editor — see SCHEMA_SQL below / DEPLOY.md.
Only api/ calls this; the agent never does.
"""
from datetime import datetime, timezone

import requests

from config import SUPABASE_URL, SUPABASE_SERVICE_KEY, USE_DB

_TIMEOUT = 8  # seconds — keep short; this is off the hot path but shouldn't hang


# Reference DDL (run once in Supabase → SQL Editor). Kept here as the source of truth.
SCHEMA_SQL = """
create table if not exists steam_user (
  steam_id   text primary key,
  vanity     text,
  persona    text,
  avatar     text,
  first_seen timestamptz not null default now(),
  last_seen  timestamptz not null default now()
);
create table if not exists snapshot (
  steam_id           text primary key references steam_user(steam_id) on delete cascade,
  status             text not null default 'ready',
  built_at           timestamptz not null default now(),
  games              int,
  total_achievements int,
  total_unlocked     int,
  location           text,
  error              text
);
create index if not exists snapshot_built_at_idx on snapshot (built_at);
create table if not exists query_log (
  id         bigserial primary key,
  steam_id   text references steam_user(steam_id) on delete set null,
  question   text,
  route      text,
  latency_ms int,
  created_at timestamptz not null default now()
);
create table if not exists user_memory (
  steam_id   text primary key references steam_user(steam_id) on delete cascade,
  memory     text,                                  -- evolving per-user summary (~8 bullets)
  updated_at timestamptz not null default now()
);

-- Lock the tables down. Supabase auto-exposes public-schema tables over its REST
-- API (reachable with the public anon key); RLS ON + NO policies = default-deny
-- for anon while our server's service_role key bypasses RLS and keeps full access.
alter table steam_user  enable row level security;
alter table snapshot    enable row level security;
alter table query_log   enable row level security;
alter table user_memory enable row level security;
"""


def using_db() -> bool:
    return USE_DB


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _headers(prefer: str = "") -> dict:
    h = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _post(table: str, row: dict, *, upsert_on: str | None = None) -> None:
    """Insert (or upsert when upsert_on is given) a single row. Best-effort."""
    if not using_db():
        return
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    params = {}
    prefer = "return=minimal"
    if upsert_on:
        params["on_conflict"] = upsert_on
        prefer = "return=minimal,resolution=merge-duplicates"
    try:
        requests.post(url, params=params or None, headers=_headers(prefer),
                      json=row, timeout=_TIMEOUT)
    except requests.RequestException:
        pass


# ── public hooks (called from api/) ──────────────────────────────────────────────

def upsert_user(steam_id: str, vanity: str | None = None,
                persona: str | None = None, avatar: str | None = None) -> None:
    """Record/refresh identity on connect. first_seen is set by the DB default on
    insert and left untouched on update; last_seen is bumped every call. Only
    non-None fields are written so a minimal early call doesn't wipe persona/avatar."""
    row = {"steam_id": str(steam_id), "last_seen": _now()}
    if vanity:
        row["vanity"] = vanity
    if persona:
        row["persona"] = persona
    if avatar:
        row["avatar"] = avatar
    _post("steam_user", row, upsert_on="steam_id")


def upsert_snapshot(steam_id: str, *, status: str = "ready",
                    games: int | None = None, total_achievements: int | None = None,
                    total_unlocked: int | None = None, location: str | None = None,
                    error: str | None = None) -> None:
    """Record snapshot metadata after a build (freshness + cleanup + header stats)."""
    row = {
        "steam_id": str(steam_id),
        "status": status,
        "built_at": _now(),
        "games": games,
        "total_achievements": total_achievements,
        "total_unlocked": total_unlocked,
        "location": location,
        "error": error,
    }
    _post("snapshot", row, upsert_on="steam_id")


def log_query(steam_id: str | None, question: str, route: str | None,
              latency_ms: int) -> None:
    """Append a usage/latency row per /ask. Truncates question to keep rows lean."""
    _post("query_log", {
        "steam_id": str(steam_id) if steam_id else None,
        "question": (question or "")[:500],
        "route": route,
        "latency_ms": latency_ms,
    })


def get_memory(steam_id: str) -> str:
    """The user's evolving memory summary (''  if none / DB off). Best-effort."""
    if not using_db():
        return ""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/user_memory",
            params={"steam_id": f"eq.{steam_id}", "select": "memory"},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json()
        return (rows[0].get("memory") or "") if rows else ""
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return ""


def save_memory(steam_id: str, memory: str) -> None:
    """Upsert the user's memory summary. Best-effort."""
    _post("user_memory",
          {"steam_id": str(steam_id), "memory": memory, "updated_at": _now()},
          upsert_on="steam_id")


def delete_memory(steam_id: str) -> None:
    """Wipe a user's memory (the 'Clear' control). Best-effort."""
    if not using_db():
        return
    try:
        requests.delete(
            f"{SUPABASE_URL}/rest/v1/user_memory",
            params={"steam_id": f"eq.{steam_id}"},
            headers=_headers("return=minimal"),
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        pass


def stale_snapshots(cutoff_iso: str) -> list[str]:
    """SteamIDs whose snapshot was built before `cutoff_iso` (for cache GC).
    Best-effort — returns [] on error or when the DB is off."""
    if not using_db():
        return []
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/snapshot",
            params={"select": "steam_id", "built_at": f"lt.{cutoff_iso}"},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return [row["steam_id"] for row in r.json() if row.get("steam_id")]
    except (requests.RequestException, ValueError, KeyError):
        return []


def delete_snapshot(steam_id: str) -> None:
    """Delete a snapshot METADATA row (the blob is removed separately). Identity
    (steam_user) + usage (query_log) are kept. Best-effort."""
    if not using_db():
        return
    try:
        requests.delete(
            f"{SUPABASE_URL}/rest/v1/snapshot",
            params={"steam_id": f"eq.{steam_id}"},
            headers=_headers("return=minimal"),
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        pass
