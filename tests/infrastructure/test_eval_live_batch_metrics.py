"""`_metrics_for`'s `include_reason` setting.

The live batch discards judge reasons entirely (`_score_case` stores only
`metric.score`), so paying for them is pure waste — 8 to 6 judge calls per
Docs/Web Search case. Regression guard against a future edit silently
restoring the cost.
"""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import eval_live_batch  # noqa: E402
from scripts.eval_live_batch import _metrics_for  # noqa: E402


def test_docs_case_metrics_have_reasons_disabled() -> None:
    case = {
        "route": "docs",
        "masked_text": "Where is the nearest store?",
        "answer": "Try the store locator.",
        "retrieval_context": ["some retrieved chunk"],
    }

    metrics = _metrics_for(case)
    relevancy_and_faithfulness = [m for m in metrics if hasattr(m, "include_reason")]

    assert relevancy_and_faithfulness, "expected Answer Relevancy/Faithfulness present"
    assert all(m.include_reason is False for m in relevancy_and_faithfulness)


def test_score_case_carries_the_experiment_tag_through(
    monkeypatch: Any,
) -> None:
    """The batch score must carry the same `experiment` the case was
    originally recorded under, alongside `answer_prompt_version` — both
    are `judge_stats.live_deepeval()`'s filter axes, and `.get()`, not
    `case[...]`, so a case recorded before this field existed reads as
    unknown rather than crashing the batch.
    """
    monkeypatch.setattr(eval_live_batch, "_metrics_for", lambda _case: [])
    case = {
        "trace_id": "t1",
        "route": "docs",
        "masked_text": "q",
        "answer": "a",
        "retrieval_context": [],
        "answer_prompt_version": 12,
        "experiment": "baseline-v4",
    }

    result = eval_live_batch._score_case(case, 0)

    assert result["answer_prompt_version"] == 12
    assert result["experiment"] == "baseline-v4"


def test_score_case_reads_a_missing_experiment_as_unknown(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(eval_live_batch, "_metrics_for", lambda _case: [])
    case = {
        "trace_id": "t1",
        "route": "docs",
        "masked_text": "q",
        "answer": "a",
        "retrieval_context": [],
    }

    result = eval_live_batch._score_case(case, 0)

    assert result["experiment"] is None
