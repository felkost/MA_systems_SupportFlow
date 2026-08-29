"""`SupportFlowState`'s own shape, beyond what `test_supervisor.py`'s
higher-level tests already exercise.
"""

from src.application.supervisor import build_initial_state
from src.domain.state import (
    RESET_ERRORS_MARKER,
    _errors_reducer,
    _keep_last_n_turns,
    format_conversation_history,
)
from src.kernel.constants import MAX_HISTORY_TURNS


def test_state_carries_tools_called() -> None:
    state = build_initial_state("текст", "r1", "s1", "t1")
    assert state["tools_called"] == []


def test_state_seeds_empty_conversation_history() -> None:
    state = build_initial_state("текст", "r1", "s1", "t1")
    assert state["conversation_history"] == []


def test_state_seeds_errors_with_the_reset_marker() -> None:
    # Not a real `list[ErrorType]` value — see state.py's own comment on
    # why `build_initial_state` must seed this exact marker rather than
    # a plain empty list.
    state = build_initial_state("текст", "r1", "s1", "t1")
    assert state["errors"] == RESET_ERRORS_MARKER  # type: ignore[comparison-overlap]


def test_errors_reducer_accumulates_within_a_turn() -> None:
    combined = _errors_reducer(["router_timeout"], ["docs_low_confidence"])
    assert combined == ["router_timeout", "docs_low_confidence"]


def test_errors_reducer_resets_on_the_turn_marker_instead_of_accumulating() -> None:
    accumulated_from_a_prior_turn = ["router_timeout", "docs_low_confidence"]
    reset = _errors_reducer(
        accumulated_from_a_prior_turn, RESET_ERRORS_MARKER  # type: ignore[arg-type]
    )
    assert reset == []


def test_keep_last_n_turns_appends_below_the_cap() -> None:
    existing = [{"customer": "a", "answer": "1"}]
    new = [{"customer": "b", "answer": "2"}]
    assert _keep_last_n_turns(existing, new) == [
        {"customer": "a", "answer": "1"},
        {"customer": "b", "answer": "2"},
    ]


def test_keep_last_n_turns_trims_to_the_configured_cap() -> None:
    existing = [
        {"customer": str(i), "answer": str(i)} for i in range(MAX_HISTORY_TURNS)
    ]
    new = [{"customer": "newest", "answer": "newest"}]
    result = _keep_last_n_turns(existing, new)
    assert len(result) == MAX_HISTORY_TURNS
    assert result[-1] == {"customer": "newest", "answer": "newest"}
    assert result[0] == {"customer": "1", "answer": "1"}


def test_format_conversation_history_is_empty_for_no_turns() -> None:
    assert format_conversation_history([]) == ""


def test_format_conversation_history_renders_customer_and_answer_pairs() -> None:
    turns = [{"customer": "Мене звати Фелікс", "answer": "Приємно познайомитись!"}]
    rendered = format_conversation_history(turns)
    assert "Мене звати Фелікс" in rendered
    assert "Приємно познайомитись!" in rendered
