"""`RouteCorrectnessMetric`/`PrivacySafetyMetric` — deterministic, no LLM
call, so these run synchronously and fast.
"""

from deepeval.test_case import LLMTestCase

from tests.evaluation.metrics import PrivacySafetyMetric, RouteCorrectnessMetric


def _case(actual_output: str = "x", **metadata: str) -> LLMTestCase:
    return LLMTestCase(input="q", actual_output=actual_output, metadata=metadata)


def test_route_correctness_metric_passes_on_matching_route() -> None:
    metric = RouteCorrectnessMetric()
    metric.measure(_case(expected_route="docs", actual_route="docs"))
    assert metric.score == 1.0
    assert metric.success is True


def test_route_correctness_metric_fails_on_mismatched_route() -> None:
    metric = RouteCorrectnessMetric()
    metric.measure(_case(expected_route="docs", actual_route="web_search"))
    assert metric.score == 0.0
    assert metric.success is False


def test_privacy_safety_metric_passes_on_clean_output() -> None:
    metric = PrivacySafetyMetric()
    metric.measure(_case(actual_output="Магазин працює з 8 до 22."))
    assert metric.score == 1.0
    assert metric.success is True


def test_privacy_safety_metric_fails_when_output_leaks_personal_data() -> None:
    metric = PrivacySafetyMetric()
    metric.measure(_case(actual_output="Зв'яжіться зі мною: +380671234567"))
    assert metric.score == 0.0
    assert metric.success is False
