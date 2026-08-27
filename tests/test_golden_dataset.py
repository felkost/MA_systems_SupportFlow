"""The full golden-dataset run (`deepeval test run tests/`).

Two kinds of test here, split by cost/speed — `pyproject.toml`'s
`addopts = "-m 'not eval'"` keeps the second kind out of the default
`pytest --cov=src` gate (README's Gate promise: never a live call):

- Deterministic checks on `evals/golden_dataset.json` itself — fast, free,
  run on every gate.
- `@pytest.mark.eval` — one real end-to-end case per test, real
  OpenRouter/Silpo MCP/Tavily calls (and, for an escalating case, a real
  file write and — only with `ALLOW_REAL_SEND=true` — a real Telegram
  send). Run deliberately: `deepeval test run tests/test_golden_dataset.py -m eval`.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from deepeval import assert_test

from src.domain.filters import contains_forbidden_data
from src.infrastructure.observability import experiment_tags, tag_trace
from src.kernel.settings import PROJECT_ROOT
from tests.evaluation import harness

GOLDEN_DATASET_PATH = Path(PROJECT_ROOT) / "evals" / "golden_dataset.json"
# One tag per pytest process, so this gate run's traces are distinguishable
# in Langfuse from any other run.
_RUN_TAG = f"final-gate-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"


@pytest.fixture(scope="session")
def _eval_warm_up() -> None:
    """Absorbs Docs/Web Search Agent's cold start once per pytest session,
    before the first `@pytest.mark.eval` case runs — the same protection
    `scripts/run_golden_dataset_baseline.py` always had, extended to a
    direct `pytest`/`deepeval test run -m eval` invocation.
    """
    harness.warm_up()


def _load_cases() -> list[dict]:
    raw = json.loads(GOLDEN_DATASET_PATH.read_text(encoding="utf-8"))
    return list(raw["cases"])


def test_golden_dataset_contains_no_pii() -> None:
    """Cases must use synthetic users only, checked with the same
    PII detector already trusted for the input filter and
    `PrivacySafetyMetric` — reused, not duplicated.
    """
    leaks = [
        case["id"]
        for case in _load_cases()
        for field in ("input", "expected_output")
        if contains_forbidden_data(case[field])
    ]
    assert leaks == [], f"golden-dataset cases carry real-shaped PII: {leaks}"


def test_golden_dataset_split_and_injection_counts() -> None:
    """6/6/6 typical/edge/failure, >=3 injection cases in the edge
    slice. Checked by script, not by eye.
    """
    cases = _load_cases()
    assert len(cases) == 18

    by_category: dict[str, int] = {}
    for case in cases:
        by_category[case["category"]] = by_category.get(case["category"], 0) + 1
    assert by_category == {"typical": 6, "edge": 6, "failure": 6}

    injection_count = sum(
        1 for case in cases if case["category"] == "edge" and "injection" in case["id"]
    )
    assert injection_count >= 3


def test_golden_dataset_failure_slice_covers_all_six_named_scenarios() -> None:
    """The six named failure scenarios — every one must have exactly
    one case.
    """
    expected_scenarios = {
        "insufficient-evidence",
        "out-of-domain",
        "critical-escalation",
        "silpo-mcp-unavailable",
        "oauth-error",
        "timeout",
    }
    ids = {
        case["id"].removeprefix("failure-0").split("-", 1)[1]
        for case in _load_cases()
        if case["category"] == "failure"
    }
    assert ids == expected_scenarios


@pytest.mark.eval
@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
def test_golden_dataset_case(case: dict, _eval_warm_up: None) -> None:
    test_case = harness.run_case(case)
    trace_id = (test_case.metadata or {}).get("trace_id")
    if trace_id:
        tag_trace(trace_id, [_RUN_TAG, *experiment_tags()])

    if case["expected_route"] == "reject":
        # No response field to derive a route signal from on this path —
        # asserted directly, not via RouteCorrectnessMetric.
        assert test_case.metadata["actual_route"] == "reject"
        return

    assert_test(test_case, harness.metrics_for_test_case(test_case))
