"""Offline eval harness — runs the golden question set through the agent and scores it.

Usage (from repo root):
    python -m eval.run_eval

Scoring: case-insensitive substring match. For comma-separated expected values
(e.g. Q07 with 7 game names) a question passes if ANY part is found in the answer.
"""
import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

from agent import run

GOLDEN      = Path(__file__).parent / "golden_questions.json"
RESULTS_OUT = Path(__file__).parent / "last_run.json"


def _normalize(text: str) -> str:
    """Strip markdown formatting and quotation marks, then normalise whitespace."""
    text = re.sub(r"\*+", "", text)       # **bold** / *italic*
    text = re.sub(r'["\']', "", text)     # straight and curly quotes
    return re.sub(r"\s+", " ", text).strip().lower()


def _score(expected: str, answer: str) -> bool:
    """Pass if any comma-separated part of `expected` appears in `answer`.

    Strips markdown formatting from the answer before checking so that
    **bold** text doesn't break substring matches.
    """
    answer_norm = _normalize(answer)
    parts = [_normalize(p) for p in expected.split(",")]
    return any(part in answer_norm for part in parts if part)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="Only run the first N questions (default: all)")
    parser.add_argument("--id", type=str, default=None,
                        help="Run a single question by ID, e.g. --id q08")
    args = parser.parse_args()

    questions = json.loads(GOLDEN.read_text())
    if args.id:
        questions = [q for q in questions if q["id"] == args.id]
        if not questions:
            print(f"No question found with id={args.id!r}")
            return
    elif args.limit:
        questions = questions[: args.limit]
    total = len(questions)
    passed    = 0
    records   = []

    print(f"Hundo eval — {total} questions\n")
    print(f"{'ID':<6} {'TYPE':<12} {'PASS?':<7} {'TIME':>5}s  QUESTION")
    print("-" * 85)

    for q in questions:
        qid      = q["id"]
        question = q["question"]
        expected = q["expected"]
        qtype    = q.get("type", "?")

        t0 = time.time()
        try:
            result  = run(question)
            answer  = result.get("answer") or ""
            retries = result.get("retries", 0)
            error   = result.get("last_error")
        except Exception as exc:
            answer  = ""
            retries = 0
            error   = str(exc)
        elapsed = round(time.time() - t0, 1)

        ok = _score(expected, answer)
        passed += int(ok)
        status = "PASS" if ok else "FAIL"

        print(f"{qid:<6} {qtype:<12} {status:<7} {elapsed:>5}s  {question[:55]}")
        if not ok:
            print(f"       expected : {expected[:80]}")
            print(f"       got      : {answer[:80]}")

        records.append({
            "id":        qid,
            "type":      qtype,
            "question":  question,
            "expected":  expected,
            "answer":    answer,
            "pass":      ok,
            "retries":   retries,
            "error":     error,
            "elapsed_s": elapsed,
        })

    print("-" * 85)
    rate = passed / total * 100
    print(f"\n  Score: {passed}/{total} passed  ({rate:.1f}%)\n")

    out = {
        "timestamp": datetime.now().isoformat(),
        "passed":    passed,
        "total":     total,
        "rate_pct":  round(rate, 1),
        "questions": records,
    }
    RESULTS_OUT.write_text(json.dumps(out, indent=2))
    print(f"  Full results saved -> {RESULTS_OUT}\n")


if __name__ == "__main__":
    main()
