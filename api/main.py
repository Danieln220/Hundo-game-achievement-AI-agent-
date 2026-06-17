"""FUTURE UPGRADE (React phase only) — a FastAPI backend that wraps the SAME
agent. The agent does not change; this just exposes run() over HTTP for a
React frontend (deploy this on Render, the frontend on Vercel/Netlify).

To activate: `pip install fastapi uvicorn`, uncomment, then
`uvicorn api.main:app --host 0.0.0.0 --port 8000`."""

# from fastapi import FastAPI
# from fastapi.middleware.cors import CORSMiddleware
# from pydantic import BaseModel
# from agent import run
#
# app = FastAPI()
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],   # tighten to your frontend's domain in production
#     allow_methods=["*"],
#     allow_headers=["*"],
# )
#
# class Query(BaseModel):
#     question: str
#
# @app.post("/ask")
# def ask(q: Query):
#     return run(q.question)
