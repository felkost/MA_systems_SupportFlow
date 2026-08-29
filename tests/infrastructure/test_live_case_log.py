"""Recording a live case must never cost the customer their answer, and
must never write the raw request.
"""

from pathlib import Path
from typing import Any

import src.infrastructure.live_case_log as live_case_log


def _use_tmp_path(monkeypatch: Any, tmp_path: Path) -> Path:
    path = tmp_path / "live_cases.jsonl"
    monkeypatch.setattr(live_case_log, "LIVE_CASES_PATH", path)
    return path


def test_append_then_read_round_trips_one_case(
    monkeypatch: Any, tmp_path: Path
) -> None:
    _use_tmp_path(monkeypatch, tmp_path)

    assert live_case_log.append_case(
        masked_text="де мій заказ",
        answer="ось відповідь",
        retrieval_context=["chunk"],
        tools_called=["silpo_find_products_batch"],
        route="docs",
        trace_id="abc",
        answer_prompt_version=9,
    )

    cases = live_case_log.read_cases()
    assert len(cases) == 1
    assert cases[0]["masked_text"] == "де мій заказ"
    assert cases[0]["route"] == "docs"


def test_read_cases_keeps_only_the_last_n(monkeypatch: Any, tmp_path: Path) -> None:
    _use_tmp_path(monkeypatch, tmp_path)
    for index in range(5):
        live_case_log.append_case(
            masked_text=f"q{index}",
            answer="a",
            retrieval_context=[],
            tools_called=[],
            route="docs",
            trace_id="t",
            answer_prompt_version=9,
        )

    assert [c["masked_text"] for c in live_case_log.read_cases(limit=2)] == ["q3", "q4"]


def test_read_cases_skips_a_malformed_line(monkeypatch: Any, tmp_path: Path) -> None:
    """One bad append must not cost every other case its score."""
    path = _use_tmp_path(monkeypatch, tmp_path)
    live_case_log.append_case(
        masked_text="good",
        answer="a",
        retrieval_context=[],
        tools_called=[],
        route="docs",
        trace_id="t",
        answer_prompt_version=9,
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{ not json\n")

    assert [c["masked_text"] for c in live_case_log.read_cases()] == ["good"]


def test_read_cases_is_empty_before_anything_is_recorded(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        live_case_log, "LIVE_CASES_PATH", tmp_path / "does_not_exist.jsonl"
    )

    assert live_case_log.read_cases() == []


def test_append_reports_failure_instead_of_raising(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """A failed measurement log must not fail the customer's request."""
    path = _use_tmp_path(monkeypatch, tmp_path)

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(type(path), "open", _boom)

    assert (
        live_case_log.append_case(
            masked_text="q",
            answer="a",
            retrieval_context=[],
            tools_called=[],
            route="docs",
            trace_id="t",
            answer_prompt_version=9,
        )
        is False
    )
