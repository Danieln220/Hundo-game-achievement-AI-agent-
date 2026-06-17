# Steam Achievement Analyst

A self-correcting data-analysis agent over Steam achievement data. Ask in
plain language ("which games am I closest to 100% on?") and the agent plans,
writes and runs pandas in a sandbox, validates and self-corrects, and answers
with numbers, a chart, and the reasoning trace.

## Structure

    app.py            Thin Streamlit UI — calls agent.run(), no logic
    agent/            UI-agnostic agent core
      __init__.py       run(question) — the seam every UI calls
      graph.py          LangGraph state, nodes, wiring
      sandbox.py        sandboxed code execution (security-critical)
    data_layer/       Steam Web API fetchers + snapshot cache -> DataFrames
    eval/             golden questions + offline eval harness
    api/              FUTURE: FastAPI backend (React upgrade phase)
    config.py         env-var config (works on Streamlit Cloud and Render)
    Dockerfile        containerized; Streamlit now, uvicorn later

The rule that keeps the upgrade cheap: **all agent logic lives in `agent/`;
the UI only calls `run()`.** Streamlit now; later, FastAPI wraps the same
`run()` and React calls it — no rewrite.

## Setup

1. `cp .env.example .env` and fill in your keys (never commit `.env`)
2. `pip install -r requirements.txt`
3. Build the snapshot once (Day 1): implement and run `data_layer.snapshot.build_snapshot`
4. `streamlit run app.py`

## Eval

    python -m eval.run_eval
