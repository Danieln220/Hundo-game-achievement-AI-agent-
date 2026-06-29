# Hundo — Steam Achievement Analyst

A self-correcting, agentic AI analyst for your Steam achievements. Ask in plain
English — *"which games am I closest to 100%?"*, *"build me a roadmap for Hollow
Knight"*, *"give me a full profile audit"* — and the agent **plans the analysis,
writes pandas, runs it in a sandbox, validates and fixes its own mistakes, and
answers with a chart.**

It never answers from memory: every number comes from real code run on your data,
so it can't hallucinate a fake achievement.

## Why I built it
I wanted to build a *real* agent, not a chatbot wrapper — something that grounds
every answer in executed code, corrects itself, and is safe enough to run
LLM-written code for strangers. Steam's free, official API made it a perfect,
data-rich playground for the hard parts: a secure sandbox, a self-correction
loop, and a clean path from local prototype to a deployed multi-user app.

## Features
- **Natural-language analysis** — grounded in sandboxed pandas, with a chart.
- **Roadmap builder** — tiered, verified plan to 100% any owned *or* unowned game.
- **Profile audit** — one request runs a battery of analyses into one report.
- **How-to + time-to-complete** — web-searched guides with numbered sources.
- **Easy wins / what to play next / rarest-flex / stuck detector.**
- **Remembers you** — cross-session memory: it learns your goals and preferences
  (e.g. "chasing 100% Hollow Knight", "skips multiplayer") across visits and tailors
  answers accordingly. View or clear it anytime.
- **Watch mode** — a live worker that reacts as you unlock achievements.
- **Self-correction, ambiguity handling, multi-turn memory, streaming progress.**

## Architecture
All agent logic lives in `agent/` behind one seam — `run(question)`. Every UI
(Streamlit, React+FastAPI, watch mode) just calls it.

    agent/        LangGraph agent: plan → code → sandbox → validate → self-correct → finalize
    data_layer/   Steam fetchers + snapshot cache → 3 fixed tables; storage/cache/db adapters
    api/          FastAPI backend (thin wrappers over run() / make_chart())
    web/          React + TS frontend (Vite)
    eval/         golden questions + offline scorer
    app.py        bare Streamlit dev UI

**Stack:** LangGraph · OpenAI-compatible LLM (DeepSeek) · pandas + matplotlib ·
FastAPI · React/Vite · Supabase (Storage + Postgres) · Upstash Redis · Steam Web API.

## Run locally
```bash
# 1. backend
pip install -r requirements.txt
cp .env.example .env            # fill STEAM_API_KEY, LLM_API_KEY, TAVILY_API_KEY
uvicorn api.main:app --port 8000

# 2. frontend (separate terminal)
cd web && npm install && npm run dev   # opens http://localhost:5173
```
Cloud services (Supabase, Upstash) are **optional locally** — unset, the app falls
back to local disk + an in-memory cache. The snapshot builds automatically on the
first profile load.

Bare Streamlit UI instead: `streamlit run app.py`. Offline eval: `python -m eval.run_eval`.

## Deploy
Steam-only, frontend → Vercel, API → Render, with Supabase Storage + Postgres and
Upstash Redis. See [DEPLOY.md](DEPLOY.md) for the step-by-step.
