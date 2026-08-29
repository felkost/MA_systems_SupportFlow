"""Append each answered live request to a JSONL file, so the same
offline evaluator that scores the golden dataset can later score real
traffic.

**Why a file rather than reading the traces back from Langfuse:** the
compose span's `input` is the whole compiled prompt and its `output` a
Pydantic repr (measured 2026-08-28), neither of which is the clean
question/answer pair an offline metric needs. The values are already in
hand at the end of the request; writing them is cheaper and exact.

**Why offline at all:** DeepEval's own OpenTelemetry instrumentation and
Langfuse's both attach to the global `TracerProvider`, so scoring inside
the request would split one trace in two. The scoring therefore runs as
a separate batch (`scripts/eval_live_batch.py`), and this module only
records the material it needs.

Only the masked request text is stored — never the raw message.
"""

import json
from pathlib import Path
from typing import Any

from src.kernel.settings import PROJECT_ROOT

LIVE_CASES_PATH = Path(PROJECT_ROOT) / "output" / "live_cases.jsonl"


def append_case(
    *,
    masked_text: str,
    answer: str,
    retrieval_context: list[str],
    tools_called: list[str],
    route: str,
    trace_id: str,
    answer_prompt_version: int | None,
) -> bool:
    """Record one answered request.

    Parameters
    ----------
    masked_text : str
        The already-masked customer message. The raw text must never
        reach this function — the state carries only the masked form.
    answer : str
        What the customer was shown.
    retrieval_context : list of str
        Needed by `FaithfulnessMetric`; empty for routes that retrieved
        nothing.
    tools_called, route, trace_id : ...
        Recorded for slicing a later batch by route and for tying a
        score back to its own trace.
    answer_prompt_version : int or None
        The resolved Langfuse version of whichever prompt composed
        `answer` — `SupportFlowState.answer_prompt_version`, `None` for
        an escalated case. Recorded so a future batch score can be
        attributed to the prompt version that actually produced it,
        rather than pooling every version's answers into one running
        mean with no way to separate them after a prompt changes —
        `judge_stats.live_deepeval()` does not yet filter by this field,
        the same way `live_quality()` filters by experiment tag; it only
        records the fact for now.

    Returns
    -------
    bool
        Whether the line was written. Never raises: a failed measurement
        log must not fail the customer's request.
    """
    try:
        LIVE_CASES_PATH.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {
            "masked_text": masked_text,
            "answer": answer,
            "retrieval_context": retrieval_context,
            "tools_called": tools_called,
            "route": route,
            "trace_id": trace_id,
            "answer_prompt_version": answer_prompt_version,
        }
        with LIVE_CASES_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def read_cases(limit: int | None = None) -> list[dict[str, Any]]:
    """The most recently recorded cases, oldest first.

    Parameters
    ----------
    limit : int or None
        Keep only the last `limit` records. `None` reads all of them.

    Returns
    -------
    list of dict
        Empty when nothing has been recorded yet. A malformed line is
        skipped rather than aborting the batch — one bad append should
        not cost every other case its score.
    """
    if not LIVE_CASES_PATH.exists():
        return []
    cases: list[dict[str, Any]] = []
    for line in LIVE_CASES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return cases[-limit:] if limit else cases
