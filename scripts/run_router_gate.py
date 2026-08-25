"""Stage 1 Go/No-Go: Router classification accuracy on the held-out gate
set (docs/decisions.md #17). Makes real OpenRouter calls — a paid,
budget-consuming step handed over to the author to run, never executed
automatically.

>= 3 runs against the n=12 labelled set in
tests/fixtures/router_classification_cases.json, reporting per-run
accuracy and the range rather than a single number, with the pinned
model id (config/models.yaml) recorded alongside every figure.

Run manually (needs OPENROUTER_API_KEY and Langfuse keys in .env):

    .venv/Scripts/python scripts/run_router_gate.py
"""

import json
import os
import statistics
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
CASES_PATH = PROJECT_ROOT / "tests" / "fixtures" / "router_classification_cases.json"
OUTPUT_PATH = PROJECT_ROOT / "output" / "router_gate_result.json"
RUNS = 3
PASS_THRESHOLD = 10  # of 12 — docs/decisions.md #17, not a literal "10/10"


def _load_env() -> None:
    if os.environ.get("OPENROUTER_API_KEY"):
        return
    for line in (PROJECT_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            if value.strip():
                os.environ[key.strip()] = value.strip()


def _run_once(cases: list[dict]) -> dict:
    from src.application.router_agent import run_router
    from src.kernel.settings import load_agent_config

    model_id = load_agent_config("router").model
    results = []
    for case in cases:
        result = run_router(
            case["input"],
            request_id=str(uuid.uuid4()),
            session_id="router-gate",
            trace_id=str(uuid.uuid4()),
        )
        correct = (
            result.classification is not None
            and result.classification.category == case["category"]
            and result.classification.urgency == case["urgency"]
        )
        results.append(
            {
                "input": case["input"],
                "expected": {"category": case["category"], "urgency": case["urgency"]},
                "actual": (
                    None
                    if result.classification is None
                    else {
                        "category": result.classification.category,
                        "urgency": result.classification.urgency,
                        "language": result.classification.language,
                    }
                ),
                "correct": correct,
                "errors": result.errors,
            }
        )
    accuracy = sum(r["correct"] for r in results) / len(results)
    return {"model": model_id, "accuracy": accuracy, "cases": results}


def main() -> None:
    _load_env()
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = data["cases"]

    runs = [_run_once(cases) for _ in range(RUNS)]
    accuracies = [r["accuracy"] for r in runs]
    model_id = runs[0]["model"]

    summary = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "model": model_id,
        "n_cases": len(cases),
        "n_runs": RUNS,
        "pass_threshold": f"{PASS_THRESHOLD}/{len(cases)}",
        "per_run_correct": [round(a * len(cases)) for a in accuracies],
        "per_run_accuracy": accuracies,
        "accuracy_mean": statistics.mean(accuracies),
        "accuracy_range": [min(accuracies), max(accuracies)],
        "passed": all(round(a * len(cases)) >= PASS_THRESHOLD for a in accuracies),
        "runs": runs,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Model: {model_id}")
    for i, correct in enumerate(summary["per_run_correct"], 1):
        print(f"Run {i}: {correct}/{len(cases)}")
    print(f"Range: {summary['accuracy_range']}, mean: {summary['accuracy_mean']:.3f}")
    verdict = "PASS" if summary["passed"] else "FAIL"
    print(f"Go/No-Go ({summary['pass_threshold']} every run): {verdict}")
    print(f"Full result written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
