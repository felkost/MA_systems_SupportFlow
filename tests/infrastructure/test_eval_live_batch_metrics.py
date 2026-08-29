"""`_metrics_for`'s `include_reason` setting.

The live batch discards judge reasons entirely (`_score_case` stores only
`metric.score`), so paying for them is pure waste — 8 to 6 judge calls per
Docs/Web Search case. Regression guard against a future edit silently
restoring the cost.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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
