"""Golden-dataset case runner. Dispatches each case to one of two paths
and returns a populated `deepeval.test_case.LLMTestCase`:

- The 3 `fault_injection` cases (OAuth error, generic Silpo MCP
  unavailable, timeout) run in-process, `docs_node` called directly with
  `graph_nodes.call_docs_agent` replaced at the layer the real fix
  actually lives at — a real A2A hop to a real Docs Agent process cannot
  be made to fail on demand.
- Every other case (15 of 18, including the `reject` case) runs
  `supervisor.handle_request` normally — the real 4-process topology when
  the launcher has started Docs/Web Search Agent as separate processes,
  since `handle_request` reaches them only over the real A2A hop either
  way.

`run_case()` itself makes real LLM/Telegram/file-write calls for every
case except the dispatch decision — it is exercised for real only by the
deliberate, permission-gated `deepeval test run tests/test_golden_dataset.py`
invocation, never by the fast `pytest --cov=src` gate. The gate's own test
(`tests/evaluation/test_harness.py`) covers the dispatch logic and the
pure `_actual_route` signal by mocking the two execution paths, not by
running them.
"""

import asyncio
from contextlib import contextmanager
from typing import Any, Callable, Iterator

import yaml
from deepeval.metrics import (
    AnswerRelevancyMetric,
    BaseMetric,
    FaithfulnessMetric,
    GEval,
    ToolCorrectnessMetric,
)
from deepeval.models import OpenRouterModel
from deepeval.test_case import LLMTestCase, SingleTurnParams, ToolCall

from src.application import graph_nodes, supervisor
from src.application.docs_agent import DocsAgentResult, run_docs_agent
from src.domain.state import SupportFlowState
from src.infrastructure.a2a_transport import A2ATimeoutError
from src.infrastructure.docs_client import DocsCallResult, DocsUnavailableError
from src.infrastructure.observability import new_trace_id
from src.kernel.settings import MODELS_CONFIG_PATH, settings
from tests.evaluation.metrics import PrivacySafetyMetric, RouteCorrectnessMetric

# Mocked at the graph-node boundary, same technique for both — the real
# fix (docs_a2a_server.py's own except branch) is proven separately by
# tests/interfaces/test_docs_a2a_server.py, not re-derived here.
_FAULT_MESSAGES = {
    "oauth_error": "SilpoMcpAuthRequiredError: no cached token, no automated login",
}


def _actual_route(state: SupportFlowState) -> str:
    """Post-hoc route signal: `next_action` alone cannot distinguish a
    Docs success from a Web Search success (both read "respond"), and
    the three response fields are not mutually exclusive
    on the escalate path (a low-confidence Docs answer sets both
    `docs_response` and, once `escalate_node` runs, `escalation_output`)
    — so `escalation_output` must be checked first.
    """
    if state["next_action"] == "reject":
        return "reject"
    if state["escalation_output"] is not None:
        return "escalate"
    if state["docs_response"] is not None:
        return "docs"
    if state["web_search_response"] is not None:
        return "web_search"
    raise ValueError(f"no route signal in terminal state: {state!r}")


def _state_to_test_case(case: dict[str, Any], state: SupportFlowState) -> LLMTestCase:
    return LLMTestCase(
        input=case["input"],
        actual_output=state["answer"] or "",
        retrieval_context=state["retrieval_context"],
        tools_called=[ToolCall(name=name) for name in state["tools_called"]],
        expected_tools=[ToolCall(name=name) for name in case.get("expected_tools", [])],
        metadata={
            "expected_route": case["expected_route"],
            "actual_route": _actual_route(state),
            "trace_id": state["trace_id"],
            # So a baseline run can record which prompt version answered
            # each case instead of that being an unrecorded inference —
            # `None` for a route with no prompt version (reject, or
            # Escalation, whose own prompt version is not yet tracked;
            # see `docs/decisions.md` #75).
            "answer_prompt_version": state["answer_prompt_version"],
        },
    )


def _run_and_escalate_if_needed(
    node_fn: Callable[[SupportFlowState], dict[str, Any]], case: dict[str, Any]
) -> SupportFlowState:
    state = supervisor.build_initial_state(
        case["input"], case["id"], case["id"], case["id"]
    )
    state.update(node_fn(state))  # type: ignore[typeddict-item]
    if state["next_action"] == "escalate":
        state.update(graph_nodes.escalate_node(state))  # type: ignore[typeddict-item]
    return state


async def _failing_search_products(
    _query: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    raise ConnectionError("Silpo MCP unavailable")


def _silpo_unavailable_call_docs_agent(
    query: str, *_args: Any, **_kwargs: Any
) -> DocsCallResult:
    result: DocsAgentResult = asyncio.run(
        run_docs_agent(query, search_products=_failing_search_products)
    )
    return DocsCallResult(
        response=result.response,
        retrieval_context=result.retrieval_context,
        tools_called=result.tools_called,
    )


def _oauth_error_call_docs_agent(*_args: Any, **_kwargs: Any) -> DocsCallResult:
    raise DocsUnavailableError(_FAULT_MESSAGES["oauth_error"])


def _timeout_call_docs_agent(*_args: Any, **_kwargs: Any) -> DocsCallResult:
    raise A2ATimeoutError("deadline exceeded")


# Each replacement injects the fault at the exact layer it's named for —
# `silpo_unavailable` below `call_docs_agent` (inside `run_docs_agent`'s
# own degrade path), `oauth_error`/`timeout` at `call_docs_agent` itself
# (the signal both legitimately cross the A2A hop as).
_FAULT_CALL_DOCS_AGENT: dict[str, Callable[..., DocsCallResult]] = {
    "silpo_unavailable": _silpo_unavailable_call_docs_agent,
    "oauth_error": _oauth_error_call_docs_agent,
    "timeout": _timeout_call_docs_agent,
}


def _run_in_process(case: dict[str, Any]) -> LLMTestCase:
    fault = case["fault_injection"]
    replacement = _FAULT_CALL_DOCS_AGENT.get(fault)
    if replacement is None:
        raise ValueError(f"unrecognised fault_injection: {fault!r}")

    original = graph_nodes.call_docs_agent
    graph_nodes.call_docs_agent = replacement  # type: ignore[assignment]
    try:
        state = _run_and_escalate_if_needed(graph_nodes.docs_node, case)
    finally:
        graph_nodes.call_docs_agent = original  # type: ignore[assignment]
    return _state_to_test_case(case, state)


def _run_live(case: dict[str, Any]) -> LLMTestCase:
    # `case["id"]` (e.g. "typical-01") is human-readable, not a valid
    # Langfuse trace id — fine for request_id/session_id, but
    # `TraceContext` runs `int(trace_id, 16)` on this value unconditionally
    # when tracing is enabled and raises `ValueError` on anything that
    # isn't 32 lowercase hex chars (`new_trace_id()`'s own docstring).
    # Live-confirmed 2026-08-26: this crashed every non-fault-injected
    # case under `TRACING_ENABLED=true` before this fix.
    state = supervisor.handle_request(
        case["input"], case["id"], case["id"], new_trace_id()
    )
    return _state_to_test_case(case, state)


@contextmanager
def _bypass_hitl() -> Iterator[None]:
    """Without this, an escalating golden case (guaranteed to exist — the
    dataset's own required content) blocks on `escalation_agent.py`'s
    interactive confirm prompt the first time this runs
    non-interactively. Scoped with try/finally, never a permanent
    module-level mutation, and deliberately leaves `settings.allow_real_send`
    untouched — two different flags for two different questions: this
    only skips the confirm prompt, it never permits a real Telegram send.
    """
    original = settings.bypass_hitl
    settings.bypass_hitl = True
    try:
        yield
    finally:
        settings.bypass_hitl = original


_WARMUP_QUERIES = ("Чи є у вас молоко?", "Коли ви працюєте?")


def warm_up() -> None:
    """Absorb Docs/Web Search Agent's cold start (`sentence-transformers`
    lazy-loaded on first request) before any case that actually counts is
    scored — moved here from `scripts/run_golden_dataset_baseline.py`'s
    own `_warm_up()` so `deepeval test run tests/test_golden_dataset.py
    -m eval` gets the same protection a baseline script run always had.
    Best-effort: a throwaway call failing here must never fail the whole
    run.
    """
    with _bypass_hitl():
        for query in _WARMUP_QUERIES:
            try:
                # "warmup" is fine for request_id/session_id but not for
                # trace_id — `new_trace_id()`'s own docstring documents
                # the 32-hex-char requirement; a literal string there
                # crashed silently through this same except before this
                # was understood.
                supervisor.handle_request(query, "warmup", "warmup", new_trace_id())
            except Exception as exc:  # noqa: BLE001 — best-effort, never fatal
                print(f"  warm-up call failed (continuing anyway): {exc}")


def run_case(case: dict[str, Any]) -> LLMTestCase:
    """The only branch point — everything else about a case (which
    metrics apply, thresholds) is decided by the caller
    (`tests/test_golden_dataset.py`), not here.
    """
    with _bypass_hitl():
        if case.get("fault_injection"):
            return _run_in_process(case)
        return _run_live(case)


# THIS is the one place every DeepEval metric threshold lives.
# Thresholds are set from the first full baseline run, not asserted in
# advance, so every value below starts as an orientation FLOOR, not a
# target. After
# `scripts/run_golden_dataset_baseline.py` produces
# `output/deepeval_baseline.json`, replace these four numbers with the
# measured baseline (never lower than the floor) — nowhere else
# in this codebase reads a threshold for these metrics.
ANSWER_RELEVANCY_THRESHOLD = 0.70
FAITHFULNESS_THRESHOLD = 0.75
SUPPORT_RESOLUTION_QUALITY_THRESHOLD = 0.70
ROUTE_AND_PRIVACY_THRESHOLD = 1.0  # deterministic metrics — pass means exactly correct


def _support_resolution_quality_metric(model: OpenRouterModel) -> GEval:
    """A custom GEval metric with no DeepEval equivalent: "чи відповідь
    вирішує проблему, пояснює обмеження та
    пропонує правильний наступний крок" (does the answer resolve the
    problem, explain limitations, and propose the right next step) —
    applies to any case with a real customer-facing answer, including an
    escalation's own `customer_message` ("ми передали ваш запит
    оператору" is itself a valid resolution — a clear next step, even
    when the system itself couldn't resolve the issue).
    """
    return GEval(
        name="Support Resolution Quality",
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        evaluation_steps=[
            "Determine whether the response resolves the customer's stated "
            "problem, or clearly explains why it cannot be resolved right now.",
            "Check whether the response is honest about its own limitations "
            "(e.g. it does not invent a fact it has no evidence for).",
            "Check whether the response proposes a concrete, correct next "
            "step for the customer (an answer, a place to check, or — for "
            "an escalation — that a human operator will follow up).",
        ],
        threshold=SUPPORT_RESOLUTION_QUALITY_THRESHOLD,
        model=model,
    )


def _judge_model() -> OpenRouterModel:
    """Reuse `config/models.yaml`'s existing `judge` entry — not part of
    `load_agent_config`'s `AgentRole` set (it has no timeout/port/etc.
    fields), so read directly here.

    Notes
    -----
    `raw["judge"]["temperature"]` — not `.get(..., 0)` — deliberately: the
    whole point of stating this value in config is that a judge's
    determinism must stop depending on an unstated library default. A
    silent fallback here would just rebuild that problem one level up.
    """
    raw = yaml.safe_load(MODELS_CONFIG_PATH.read_text(encoding="utf-8"))
    return OpenRouterModel(
        model=raw["judge"]["model"],
        temperature=raw["judge"]["temperature"],
        api_key=settings.openrouter_api_key,
    )


def metrics_for_test_case(test_case: LLMTestCase) -> list[BaseMetric]:
    """`PrivacySafetyMetric` and `RouteCorrectnessMetric` apply to every
    case except a `reject`-expected one (asserted directly on
    `next_action` in the test body instead — there is no response field
    to derive a route signal from on that path). `Support Resolution
    Quality` (GEval)
    applies to every case with a real answer — same "not reject" gate,
    since a rejected request never reaches Escalation/Docs/Web Search and
    so never produces a `customer_message`/`answer` to grade at all.
    `AnswerRelevancyMetric`/`FaithfulnessMetric` attach only when the
    case's *actual* route (not merely its expected one — a "typical" Docs
    case can still escalate) touched Docs or Web Search —
    `FaithfulnessMetric` hard-requires a non-`None` `retrieval_context`
    for a case whose route never got that far (confirmed against the
    installed SDK: empty, not `None`, is what a Router/Escalation-only
    case actually
    produces, but attaching the metric there would still only ever score
    a meaningless vacuous claim).
    """
    metrics: list[BaseMetric] = [
        PrivacySafetyMetric(threshold=ROUTE_AND_PRIVACY_THRESHOLD)
    ]
    is_reject = (
        test_case.metadata is not None
        and test_case.metadata["expected_route"] == "reject"
    )
    if not is_reject:
        metrics.append(RouteCorrectnessMetric(threshold=ROUTE_AND_PRIVACY_THRESHOLD))
        metrics.append(_support_resolution_quality_metric(_judge_model()))
    actual_route = (test_case.metadata or {}).get("actual_route")
    if actual_route == "docs":
        # `evals/golden_dataset.json`'s own top comment: `expected_tools`
        # names only the terminal Silpo MCP tool a product query should
        # reach — it has no vocabulary for Web Search Agent's own
        # `tools_called` (the search provider name, "tavily"/"duckduckgo"),
        # so this metric only means what the dataset intends when the
        # actual route is `docs`. Live-confirmed 2026-08-26: attaching it
        # for `web_search` too failed every such case on a provider-name
        # "tool" the dataset never anticipated.
        #
        # `model` is required by DeepEval's constructor but never actually
        # invoked in our usage (no `available_tools` passed, so the
        # LLM-judged "tool selection" half of this metric never runs —
        # confirmed by reading the installed deepeval==4.1.10 source; the
        # score itself is a deterministic list comparison, matching this
        # module's own "no fuzzy-judgment metric pays for an LLM" stance).
        # `should_exact_match=False` default — extra bootstrap tool
        # names in `tools_called`
        # shouldn't fail a case whose `expected_tools` already matched.
        metrics.append(
            ToolCorrectnessMetric(
                threshold=ROUTE_AND_PRIVACY_THRESHOLD, model=_judge_model()
            )
        )
    if actual_route in ("docs", "web_search"):
        model = _judge_model()
        metrics.append(
            AnswerRelevancyMetric(model=model, threshold=ANSWER_RELEVANCY_THRESHOLD)
        )
        metrics.append(
            FaithfulnessMetric(model=model, threshold=FAITHFULNESS_THRESHOLD)
        )
    return metrics
