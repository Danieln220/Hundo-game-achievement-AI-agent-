# Deploying Hundo

Steam-only public deploy: **frontend → Vercel**, **API → Render**.
All secrets live on Render; Vercel only gets the public API URL.

> Deploy gate: ship after **15.3** so the public URL is rate-limited from the
> start. Async build (15.4), Postgres (15.5), and final hardening (15.6) land on
> the live service afterwards.

## 0. Provision the managed services (free tiers)

- **Supabase** (object storage, Step 15.2): create a project. Under *Storage*,
  create two buckets — `hundo-charts` (**public**) and `hundo-snapshots`
  (**private**). Grab the project URL + the **service_role** key (Settings → API).
- **Supabase Postgres** (lean DB, Step 15.5): in the **SQL Editor**, run the
  schema in [data_layer/db.py](data_layer/db.py) (`SCHEMA_SQL` — creates
  `steam_user`, `snapshot`, `query_log` **and enables RLS** on all three). Same
  project/creds as Storage; no extra env vars. Keep RLS **on with no policies**:
  that default-denies the public anon key while the server's service_role key
  bypasses RLS and keeps full access (also clears Supabase's "RLS disabled" warning).
  **Existing deployments: re-run `SCHEMA_SQL` after Step 19.4** — it's idempotent
  and adds the `query_log` token columns (`llm_calls`, `prompt_tokens`,
  `completion_tokens`, `cache_hit_tokens`). Until then, token stats are dropped
  but lean rows keep logging (automatic fallback).
- **Upstash** (Redis, Step 15.3): create a Redis database. Copy its
  **REST URL** + **REST token** (not the TCP connection string).

All are optional locally — unset, the app falls back to local disk + in-memory,
and DB writes silently no-op (identity/usage logging just isn't recorded).

## 1. Backend → Render

1. Push this repo to GitHub.
2. Render → **New → Blueprint**, select the repo. It reads [render.yaml](render.yaml)
   and creates the `hundo-api` Docker web service (health check `/health`).
3. When prompted, paste the secrets (all marked `sync:false`):
   | Key | Value |
   |---|---|
   | `STEAM_API_KEY` | Steam Web API key — https://steamcommunity.com/dev/apikey |
   | `LLM_API_KEY` | DeepSeek API key |
   | `TAVILY_API_KEY` | Tavily key |
   | `SUPABASE_URL` | `https://<project>.supabase.co` |
   | `SUPABASE_SERVICE_KEY` | Supabase service_role key |
   | `UPSTASH_REDIS_REST_URL` | Upstash REST URL |
   | `UPSTASH_REDIS_REST_TOKEN` | Upstash REST token |
   | `CORS_ORIGINS` | leave blank for now; set in step 3 |
   | `PUBLIC_API_URL` | this service's URL, e.g. `https://hundo-api.onrender.com` (for Steam sign-in) |
4. Deploy. Note the service URL, e.g. `https://hundo-api.onrender.com`.
   Verify: open `<url>/health` → `{"status":"ok","missing_secrets":[]}`.

   Rate limits (per IP) default to 15/min + 150/day for asks and 10/min for
   `/session`; override via `RATE_LIMIT_*` env vars if needed.

## 2. Frontend → Vercel

1. Vercel → **New Project**, import the repo, set **Root Directory = `web`**
   (it auto-detects Vite via [web/vercel.json](web/vercel.json)).
2. Add an env var: `VITE_API_URL = https://hundo-api.onrender.com` (your Render URL).
   *Vite bakes `VITE_*` at build time — redeploy if you change it.*
3. Deploy. Note the URL, e.g. `https://hundo.vercel.app`.

## 3. Wire CORS

Back in Render, set `CORS_ORIGINS = https://hundo.vercel.app` and redeploy the API.
Open the Vercel URL, load a profile, ask a question — done.

## Notes
- **Render free tier** sleeps after inactivity → first request is a cold start
  (~30s + the snapshot build). Hardened in 15.6.
- Charts + snapshots persist in Supabase Storage (15.2), so they survive
  redeploys. Without the Supabase env vars they'd fall back to the container's
  ephemeral disk.
- Rate limiting + the snapshot build-lock use Upstash (15.3). Without the Upstash
  env vars the API falls back to an in-memory limiter (single-instance only).
