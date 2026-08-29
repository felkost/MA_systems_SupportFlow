"""Aggregate judge scores for the chat UI's quality footer: the live
LLM-as-a-judge scores for the current configuration, and the frozen
golden-dataset baseline they are read against.

Kept out of `observability.py` so that module stays about producing
traces rather than reading them back. That split no longer keeps this
file under the 250-line band by itself — two `experiment`/
`answer_prompt_version` filter fixes pushed it to 359 lines, a known
exception pending a follow-up split.

**Why the live figure is filtered by experiment tag rather than simply
taking the most recent scores:** measured 2026-08-28, the project's
Langfuse account held 160 `supportflow-answer-relevance` scores, of
which 138 came from offline scripts (their root trace is
`docs_agent.compose` itself, carrying no tags) and 21 from a tagged
golden-dataset baseline run. Taking "the last N" would have averaged the
tail of `scripts/compare_prompt_versions.py`'s docs run — half of whose
calls used the `candidate` prompt that was measured and then rejected —
and labelled the result as the live system's answer quality. Filtering to
the configured experiment's own tag is what makes the number describe the
population its caption claims.
"""

import json
import statistics
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.domain.statistics import bootstrap_interval
from src.infrastructure.live_case_log import read_cases
from src.infrastructure.observability import experiment_tags, get_langfuse_client
from src.infrastructure.prompts import get_prompt
from src.kernel.settings import PROJECT_ROOT, settings

# Which prompt's version a route's answer must be checked against — the
# same reasoning `live_quality`'s own experiment-tag filter documents
# above, applied to `live_deepeval`'s pool instead: a case answered by a
# since-replaced prompt version describes that old version, not the one
# currently live, no matter how recently it was graded.
_ROUTE_PROMPT_NAME = {
    "docs": "supportflow/docs",
    "web_search": "supportflow/web_search",
}

# Both live evaluators, each scoped to its own agents: relevance covers
# Docs/Web Search, handover quality covers Escalation. Escalation cannot
# score well on "does this answer the customer" by construction, which is
# why they are separate rules — and why the UI must never average them.
LIVE_SCORE_NAMES = (
    "supportflow-answer-relevance",
    "supportflow-escalation-quality",
)

# Every numeric metric the baseline run recorded. Offered in full rather
# than reduced to one: they measure different properties (relevance,
# grounding, routing, privacy), and picking a single one for the reader
# would hide the others behind an unexplained choice.
_BASELINE_PATH = Path(PROJECT_ROOT) / "output" / "deepeval_baseline.json"
LIVE_EVAL_PATH = Path(PROJECT_ROOT) / "output" / "live_eval.json"

# A read of an external service on a page load: bounded so an unreachable
# or slow Langfuse degrades the footer instead of hanging the chat UI.
_FETCH_TIMEOUT_SECONDS = 10


def summarize(values: list[float]) -> dict[str, Any]:
    """Mean, spread, and range of a score sample, honest about small `n`.

    Parameters
    ----------
    values : list of float

    Returns
    -------
    dict
        `n`, `mean`, `std_dev`, and `ci` — a 95% bootstrap interval as a
        two-element list. `mean` is `None` for an empty sample; `std_dev`
        and `ci` are additionally `None` at `n == 1`, where
        `statistics.stdev` raises `StatisticsError` rather than returning
        zero — a single observation has no measured spread, and reporting
        0.0 would claim perfect consistency from one data point.

    Notes
    -----
    The interval is reported instead of the min–max range because the
    range answers a question nobody asked ("what was the worst single
    case") while inviting the one that matters ("is this mean different
    from that one") to be answered by eye. Measured 2026-08-28: live and
    baseline resolution-quality means of 0.560 and 0.672 look like a real
    gap and are not — the interval around their difference spans zero.

    Examples
    --------
    >>> summarize([])["n"]
    0
    >>> summarize([0.6])["ci"] is None
    True
    >>> round(summarize([0.6, 0.8])["mean"], 2)
    0.7
    """
    if not values:
        return {"n": 0, "mean": None, "std_dev": None, "ci": None}
    if len(values) == 1:
        return {"n": 1, "mean": values[0], "std_dev": None, "ci": None}
    low, high = bootstrap_interval(values)
    return {
        "n": len(values),
        "mean": statistics.mean(values),
        "std_dev": statistics.stdev(values),
        "ci": [low, high],
    }


def live_quality(limit: int = 20) -> dict[str, Any]:
    """The most recent scores of every live evaluator, for the
    *currently configured* experiment, aggregated per evaluator.

    Parameters
    ----------
    limit : int, default=20
        Newest-first cap, applied per evaluator. Langfuse returns scores
        in descending timestamp order (confirmed live against
        `langfuse==4.14.4`).

    Returns
    -------
    dict
        `available` plus, when true, the `experiment` tag the sample was
        drawn from and a `metrics` map of evaluator name to its
        `summarize` fields. `available` is false with a `reason` when
        tracing is off, no experiment is configured, or the read failed.

    Notes
    -----
    Kept per evaluator rather than pooled: the two rubrics grade
    different things on different agents, so a combined mean would
    average two incomparable populations — the exact defect that split
    them into separate rules in the first place.

    Fails closed on a missing experiment tag: with no tag to filter by,
    the only alternative is the unfiltered pool that mixes live answers
    with offline script runs, and a mixed number under a live caption is
    worse than no number.
    """
    tags = experiment_tags()
    if not tags:
        return {"available": False, "reason": "no_experiment_configured"}
    client = get_langfuse_client()
    if client is None:
        return {"available": False, "reason": "tracing_disabled"}

    experiment_tag = tags[0]
    metrics: dict[str, Any] = {}
    for score_name in LIVE_SCORE_NAMES:
        try:
            response = client.api.scores.get_many(
                name=score_name,
                trace_tags=[experiment_tag],
                limit=limit,
                request_options={"timeout_in_seconds": _FETCH_TIMEOUT_SECONDS},
            )
        except Exception:  # noqa: BLE001 — network/SDK errors vary
            # Same rule as `observability.tag_trace`: a measurement read
            # must never take down the page it is decorating.
            return {"available": False, "reason": "fetch_failed"}
        values = [
            s.value for s in response.data if getattr(s, "value", None) is not None
        ]
        metrics[score_name] = summarize(values)

    return {
        "available": True,
        "experiment": settings.experiment,
        "judge": "Langfuse",
        "metrics": metrics,
    }


def _scored_cases() -> list[dict[str, Any]]:
    """Every case the offline batch has already graded, or `[]`."""
    try:
        payload = json.loads(LIVE_EVAL_PATH.read_text(encoding="utf-8"))
        cases = payload["cases"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return []
    return cases if isinstance(cases, list) else []


def unscored_cases() -> list[dict[str, Any]]:
    """Recorded requests the offline batch has not graded yet.

    Returns
    -------
    list of dict
        Oldest first, so a capped run grades the backlog in order.

    Notes
    -----
    Matched on `trace_id`, which is what makes re-running free when
    nothing new has arrived — the batch used to re-grade a fixed window
    of recent cases and charge for work it had already done.
    """
    scored = {case.get("trace_id") for case in _scored_cases()}
    return [case for case in read_cases() if case.get("trace_id") not in scored]


def live_deepeval() -> dict[str, Any]:
    """The offline DeepEval results accumulated over live requests.

    Returns
    -------
    dict
        `available`, the `judge`, when the batch last ran, how many cases
        it has graded, how many are still `pending`, and a `metrics` map
        of metric name to its `summarize` fields plus the `covers` count
        of graded cases that metric applies to.

    Notes
    -----
    Not cached, unlike `golden_baseline`: the file grows every run.

    This is the one block that can legitimately be read against the
    golden baseline — same metrics, same judge, same rubric, two
    populations. It carries fewer metrics than the baseline by design:
    route and tool correctness need an expected answer that live traffic
    does not have.

    A metric's `covers` is below the case count whenever the metric does
    not apply to every case — faithfulness grades an answer against
    retrieved sources, and an escalation has none. Reported rather than
    levelled: forcing every metric onto every case would mean grading
    vacuous claims, and dropping cases to match the smallest metric would
    throw away real measurements.

    A case is dropped from every metric, not levelled either, when it
    fails either of two independent checks (`_is_current`):

    1. A docs/web_search case whose `answer_prompt_version` does not
       match that route's *current* `production` version — a prompt edit
       is exactly the kind of change that invalidates old answers for
       describing current quality, and `eval_live_batch.py` never
       re-scores an already-graded case. Escalation cases are exempt —
       Escalation's own prompt version is not yet recorded
       (`SupportFlowState.answer_prompt_version` is `None` for that
       route by design), so there is nothing to check them against.
    2. Any case (including Escalation) whose `experiment` does not match
       `settings.experiment`, checked only when one is actually
       configured — the same "blank means no population break" rule
       `live_quality`'s own tag filter follows. Added 2026-08-29 after
       this card and the Langfuse-scored one above were found counting
       from two different starting points: this filter used to only
       ever apply to *that* card.

    `stale_prompt_version` counts every case dropped by either check
    combined, so a sudden drop in `n_cases` right after a promotion or an
    `EXPERIMENT` bump reads as "waiting for fresh traffic", not as a
    silent shrink.
    """
    cases = _scored_cases()
    if not cases:
        return {"available": False, "reason": "no_batch_run_yet"}

    try:
        current_versions = {
            route: get_prompt(name)[1] for route, name in _ROUTE_PROMPT_NAME.items()
        }
    except Exception:  # noqa: BLE001 — a cold Langfuse cache is the only
        # way `get_prompt` raises (its own docstring); a stale cached
        # version is fine and never reaches here. Fails closed rather than
        # silently falling back to the unfiltered pool, which is exactly
        # the mixed-population defect this filter exists to prevent.
        return {"available": False, "reason": "prompt_version_unresolved"}

    def _is_current(case: dict[str, Any]) -> bool:
        # Two independent axes, both must match. Prompt version is
        # per-route (Escalation has none to check); experiment is
        # global — checked only when one is actually configured, the
        # same "blank means no population break" rule `live_quality`'s
        # own tag filter follows.
        route = case.get("route")
        if route in _ROUTE_PROMPT_NAME:
            if case.get("answer_prompt_version") != current_versions[route]:
                return False
        if settings.experiment and case.get("experiment") != settings.experiment:
            return False
        return True

    current_cases = [case for case in cases if _is_current(case)]
    stale_prompt_version = len(cases) - len(current_cases)

    try:
        measured_at = json.loads(LIVE_EVAL_PATH.read_text(encoding="utf-8")).get(
            "measured_at"
        )
    except (OSError, json.JSONDecodeError):
        measured_at = None

    by_metric: dict[str, list[float]] = {}
    for case in current_cases:
        for name, score in (case.get("scores") or {}).items():
            by_metric.setdefault(name, []).append(score)

    return {
        "available": True,
        "judge": "DeepEval",
        "measured_at": measured_at,
        "n_cases": len(current_cases),
        "stale_prompt_version": stale_prompt_version,
        "pending": len(unscored_cases()),
        # Which prompt version this series is actually measured against —
        # already resolved above for the filter itself, just exposed here
        # so the panel can label a small `n` as "new series started here"
        # instead of it reading as lost history after a deliberate prompt
        # change (2026-08-29).
        "prompt_versions": current_versions,
        "metrics": {
            name: {**summarize(values), "covers": len(values)}
            for name, values in sorted(by_metric.items())
        },
    }


@lru_cache(maxsize=1)
def golden_baseline() -> dict[str, Any]:
    """The frozen golden-dataset reference lines, one per metric.

    Returns
    -------
    dict
        `available`, the `judge` that produced the run, and a `metrics`
        map of metric name to its `summarize` fields.

    Notes
    -----
    Cached: the file is a tracked, immutable artefact of one past run, so
    re-reading it per request buys nothing. A metric's `n` can be below
    the dataset's 18 cases — a metric that does not apply to a case
    records `null` rather than a zero, and averaging those nulls as zeros
    would invent failures the run never measured.
    """
    try:
        cases = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False, "reason": "baseline_unavailable"}

    by_metric: dict[str, list[float]] = {}
    for case in cases:
        for name, measured in case.get("metrics", {}).items():
            if measured.get("score") is not None:
                by_metric.setdefault(name, []).append(measured["score"])

    return {
        "available": True,
        "judge": "DeepEval",
        "n_cases": len(cases),
        "metrics": {
            name: {**summarize(v), "covers": len(v)}
            for name, v in sorted(by_metric.items())
        },
    }
