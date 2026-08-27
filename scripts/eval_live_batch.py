"""Score recorded live requests with the same offline evaluator that
scores the golden dataset, so one instrument measures both populations.

This is the only comparison in the quality panel that is actually valid:
the live Langfuse judge and DeepEval use different rubrics on different
inputs, so their numbers cannot be read against each other, but the same
DeepEval metric on live traffic and on the golden dataset can.

**Incremental by trace id.** An earlier version re-scored a fixed window
of the most recent cases on every run, which paid a second time for
cases already graded and made the result a sliding window rather than an
accumulating one — with no natural moment to re-run it. Now each case is
stored with its own trace id and only unscored ones cost anything, so
re-running with nothing new is free and instant.

Two metrics from the golden-dataset run are deliberately absent here.
`Route Correctness` and `Tool Correctness` both compare against an
*expected* route/tool list, which live traffic does not have; deriving
"expected" from what actually happened would score 1.0 by construction
and measure nothing.

Costs real money for each *new* case — one judge call per metric — so
the count is stated before anything runs and an interactive run waits
for a typed confirmation.

    .venv/Scripts/python scripts/eval_live_batch.py
    .venv/Scripts/python scripts/eval_live_batch.py --yes --limit 10
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepeval.metrics import (  # noqa: E402
    AnswerRelevancyMetric,
    BaseMetric,
    FaithfulnessMetric,
)
from deepeval.test_case import LLMTestCase  # noqa: E402

from src.infrastructure.judge_stats import LIVE_EVAL_PATH, unscored_cases  # noqa: E402
from tests.evaluation.harness import (  # noqa: E402
    ANSWER_RELEVANCY_THRESHOLD,
    FAITHFULNESS_THRESHOLD,
    ROUTE_AND_PRIVACY_THRESHOLD,
    PrivacySafetyMetric,
    _judge_model,
    _support_resolution_quality_metric,
)

# A per-call ceiling, not a total: it bounds how long one run (and, when
# the UI button triggers it, one HTTP request) can take. Whatever is left
# over is reported and picked up by the next run.
_DEFAULT_LIMIT = 10


def _metrics_for(case: dict[str, Any]) -> list[BaseMetric]:
    """The metrics a live case can legitimately carry.

    Notes
    -----
    Relevancy and faithfulness attach only to a Docs/Web Search answer:
    faithfulness grades an answer against retrieved sources, and an
    escalation has none — grading it there would score a vacuous claim.
    """
    metrics: list[BaseMetric] = [
        PrivacySafetyMetric(threshold=ROUTE_AND_PRIVACY_THRESHOLD),
        _support_resolution_quality_metric(_judge_model()),
    ]
    if case["route"] in ("docs", "web_search"):
        model = _judge_model()
        metrics.append(
            AnswerRelevancyMetric(model=model, threshold=ANSWER_RELEVANCY_THRESHOLD)
        )
        metrics.append(
            FaithfulnessMetric(model=model, threshold=FAITHFULNESS_THRESHOLD)
        )
    return metrics


def _score_case(case: dict[str, Any], index: int) -> dict[str, Any]:
    """Grade one recorded case, keeping whatever metrics succeeded.

    A judge or provider failure drops that one metric rather than the
    whole case: a partial score is still evidence, and re-running would
    otherwise re-pay for every metric that had already worked.
    """
    test_case = LLMTestCase(
        input=case["masked_text"],
        actual_output=case["answer"],
        retrieval_context=case["retrieval_context"],
    )
    scores: dict[str, float] = {}
    for metric in _metrics_for(case):
        try:
            metric.measure(test_case)
        except Exception as exc:  # noqa: BLE001 — judge/provider errors vary
            print(f"  [{index}] {metric.__class__.__name__} failed: {exc}")
            continue
        if metric.score is not None:
            scores[str(metric.__name__)] = float(metric.score)
    return {"trace_id": case["trace_id"], "route": case["route"], "scores": scores}


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the confirmation prompt (used when the UI triggers this)",
    )
    parser.add_argument("--limit", type=int, default=_DEFAULT_LIMIT)
    args = parser.parse_args()

    pending = unscored_cases()
    if not pending:
        print("Nothing new to score — every recorded case already has a result.")
        return

    batch = pending[: args.limit]
    calls = sum(len(_metrics_for(case)) for case in batch)
    print(f"\n{len(batch)} new cases ({len(pending)} pending), {calls} judge calls.")
    if not args.yes and input("Type 'run' to proceed: ").strip() != "run":
        print("Aborted — nothing was called.")
        raise SystemExit(1)

    try:
        existing = json.loads(LIVE_EVAL_PATH.read_text(encoding="utf-8"))["cases"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        existing = []

    for index, case in enumerate(batch, start=1):
        existing.append(_score_case(case, index))
        print(f"  [{index}/{len(batch)}] {case['route']}")

    LIVE_EVAL_PATH.write_text(
        json.dumps(
            {
                "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "cases": existing,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    remaining = len(pending) - len(batch)
    print(f"\nWrote {LIVE_EVAL_PATH} — {len(existing)} cases scored, {remaining} left.")


if __name__ == "__main__":
    main()
