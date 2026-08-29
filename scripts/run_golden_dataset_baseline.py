"""The measure-only baseline run.

Runs all 18 golden-dataset cases once, scores each with the metrics
`tests.evaluation.harness.metrics_for_test_case` assigns it (this varies
per case — Faithfulness/AnswerRelevancy only for a case whose *actual*
route touched Docs/Web Search — so this script measures each metric
directly rather than through `deepeval.evaluate()`'s own bulk API, which
applies one metric list uniformly to every test case), and writes
`output/deepeval_baseline.json`. Real thresholds for
`tests/test_golden_dataset.py`'s `assert_test` calls are set FROM this
baseline — thresholds are set from the first full run, not asserted in
advance, so this script never asserts pass/fail itself.

Costs real money and time: 18 cases, at least one real OpenRouter call
each (Router + Docs/Web Search/Escalation composition), several real
Silpo MCP/Tavily calls, up to 4 judge-model calls per Docs/Web-Search case
(Answer Relevancy + Faithfulness), one real file write and (only with
`ALLOW_REAL_SEND=true`, which this script never sets) a real Telegram
send. State the expected cost/time out loud and get the author's
permission before running — this script does not ask for you.

    .venv/Scripts/python scripts/run_golden_dataset_baseline.py

Requires the real 4-process topology running for the 15 non-fault-injected
cases (`python -m src.interfaces.launcher` in a separate terminal) — the
3 fault-injected cases run in-process regardless.
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.infrastructure.observability import experiment_tags, tag_trace  # noqa: E402
from src.kernel.settings import PROJECT_ROOT  # noqa: E402
from tests.evaluation import harness  # noqa: E402

GOLDEN_DATASET_PATH = Path(PROJECT_ROOT) / "evals" / "golden_dataset.json"
BASELINE_OUTPUT_PATH = Path(PROJECT_ROOT) / "output" / "deepeval_baseline.json"


def _load_cases() -> list[dict]:
    raw = json.loads(GOLDEN_DATASET_PATH.read_text(encoding="utf-8"))
    return list(raw["cases"])


def _score_case(case: dict, run_tag: str) -> dict:
    started = time.monotonic()
    try:
        test_case = harness.run_case(case)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — a baseline run records failures, doesn't stop on one
        return {"id": case["id"], "error": f"{type(exc).__name__}: {exc}"}

    trace_id = (test_case.metadata or {}).get("trace_id")
    if trace_id:
        # Author's own request, 2026-08-26: tag every case's trace with
        # this run's own identifier so separate baseline runs (pre/post a
        # fix, or a future re-run) show up as distinct, comparable groups
        # in Langfuse's Trace Tags filter instead of one mixed pool.
        tag_trace(trace_id, [run_tag, *experiment_tags()])

    result: dict = {
        "id": case["id"],
        "expected_route": case["expected_route"],
        "actual_route": (test_case.metadata or {}).get("actual_route"),
        "actual_output": test_case.actual_output,
        "trace_id": trace_id,
        # Which prompt version actually answered this case — recorded so
        # the frozen baseline's comparability with a later live measure
        # is a fact this file states, not an inference someone has to
        # redo by hand from commit/prompt timestamps (docs/decisions.md
        # #75). `None` for Escalation (own version not yet tracked) or a
        # rejected case.
        "answer_prompt_version": (test_case.metadata or {}).get(
            "answer_prompt_version"
        ),
        "duration_seconds": None,
        "metrics": {},
    }

    if case["expected_route"] == "reject":
        result["metrics"]["route_matches"] = result["actual_route"] == "reject"
        result["duration_seconds"] = round(time.monotonic() - started, 2)
        return result

    for metric in harness.metrics_for_test_case(test_case):
        try:
            score = metric.measure(test_case)
        except (
            Exception
        ) as exc:  # noqa: BLE001 — one metric's failure isn't the whole case's
            result["metrics"][metric.__name__] = {
                "error": f"{type(exc).__name__}: {exc}"
            }
            continue
        result["metrics"][metric.__name__] = {
            "score": score,
            "reason": metric.reason,
        }

    result["duration_seconds"] = round(time.monotonic() - started, 2)
    return result


def main() -> None:
    cases = _load_cases()
    run_tag = f"baseline-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    print(f"Running the baseline over {len(cases)} golden-dataset cases.")
    print(
        "This makes real OpenRouter/Silpo MCP/Tavily calls and writes a real "
        "escalation report file for every escalating case — confirm this was "
        "run with the author's permission before continuing."
    )
    print(f"Tagging every trace '{run_tag}' — filter by this in Langfuse's Trace Tags.")

    print("Warming up Docs/Web Search Agent (retriever cold-start)...")
    harness.warm_up()
    results = [_score_case(case, run_tag) for case in cases]

    BASELINE_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_OUTPUT_PATH.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Wrote {BASELINE_OUTPUT_PATH}")

    failures = [r["id"] for r in results if "error" in r]
    if failures:
        print(f"{len(failures)} case(s) errored outright: {failures}")


if __name__ == "__main__":
    main()
