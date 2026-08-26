"""Stage 4 Wave B decision D-B6's dispatch logic and D-B1's post-hoc route
signal — both pure/mockable, so this file makes no live LLM/Telegram/file
call and is safe for the fast `pytest --cov=src` gate. `_run_in_process`/
`_run_live`'s own real execution is exercised only by the deliberate,
permission-gated `deepeval test run tests/test_golden_dataset.py` run.
"""

import pytest
from deepeval.metrics import FaithfulnessMetric

from src.application.supervisor import build_initial_state
from tests.evaluation import harness
from tests.evaluation.metrics import RouteCorrectnessMetric


def test_run_case_sets_bypass_hitl_true_and_never_overrides_allow_real_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FB3 / Lane 4 finding #2: an automated golden-dataset run must not
    hang on `escalation_agent.py`'s interactive confirm prompt, and must
    not silently start permitting real Telegram sends either — these are
    decision #19's two independent flags, and only one of them is this
    harness's business to touch.
    """
    from src.kernel.settings import settings

    seen_bypass_hitl: list[bool] = []

    def _fake_run_live(case: dict) -> str:
        seen_bypass_hitl.append(settings.bypass_hitl)
        return "x"  # type: ignore[return-value]

    monkeypatch.setattr(harness, "_run_live", _fake_run_live)
    assert settings.bypass_hitl is False
    assert settings.allow_real_send is False

    harness.run_case({"input": "q", "fault_injection": None, "expected_route": "docs"})

    assert seen_bypass_hitl == [True]
    assert settings.bypass_hitl is False  # restored, not left permanently on
    assert settings.allow_real_send is False  # never touched by this harness


def test_fault_injected_cases_run_in_process_live_cases_run_over_a2a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        harness, "_run_in_process", lambda case: calls.append("in_process") or "x"
    )
    monkeypatch.setattr(harness, "_run_live", lambda case: calls.append("live") or "x")

    harness.run_case({"input": "q", "fault_injection": "oauth_error"})
    harness.run_case({"input": "q", "fault_injection": None, "expected_route": "docs"})

    assert calls == ["in_process", "live"]


def test_actual_route_reads_reject_directly_from_next_action() -> None:
    state = build_initial_state("текст", "r1", "s1", "t1")
    state["next_action"] = "reject"

    assert harness._actual_route(state) == "reject"


def test_actual_route_prefers_escalation_output_over_docs_response() -> None:
    """docs_low_confidence → escalate leaves BOTH `docs_response` and
    `escalation_output` populated (round-2 correction to D-B1's "mutually
    exclusive" claim, refuted by adversarial review) — escalation must win.
    """
    from src.domain.schemas import DocsResponse, EscalationOutput

    state = build_initial_state("текст", "r1", "s1", "t1")
    state["docs_response"] = DocsResponse(answer="a", sources=[], confidence=0.2)
    state["escalation_output"] = EscalationOutput(
        summary="s", category="product", customer_message="m", attempted_resolution="r"
    )

    assert harness._actual_route(state) == "escalate"


def test_actual_route_reports_docs_on_a_confident_docs_answer() -> None:
    from src.domain.schemas import DocsResponse

    state = build_initial_state("текст", "r1", "s1", "t1")
    state["docs_response"] = DocsResponse(answer="a", sources=[], confidence=0.9)

    assert harness._actual_route(state) == "docs"


def _test_case(expected_route: str, actual_route: str):
    from deepeval.test_case import LLMTestCase

    return LLMTestCase(
        input="q",
        actual_output="a",
        metadata={"expected_route": expected_route, "actual_route": actual_route},
    )


def test_case_to_metric_mapping_skips_faithfulness_for_escalation_only_case() -> None:
    """FB2: attaching `FaithfulnessMetric` to a case whose route never
    reached Docs/Web Search would only ever score a meaningless claim.
    """
    metrics = harness.metrics_for_test_case(
        _test_case(expected_route="escalate", actual_route="escalate")
    )
    assert not any(isinstance(m, FaithfulnessMetric) for m in metrics)


def test_case_to_metric_mapping_attaches_faithfulness_for_a_docs_case() -> None:
    metrics = harness.metrics_for_test_case(
        _test_case(expected_route="docs", actual_route="docs")
    )
    assert any(isinstance(m, FaithfulnessMetric) for m in metrics)


def test_case_to_metric_mapping_skips_route_correctness_for_reject_case() -> None:
    metrics = harness.metrics_for_test_case(
        _test_case(expected_route="reject", actual_route="reject")
    )
    assert not any(isinstance(m, RouteCorrectnessMetric) for m in metrics)
