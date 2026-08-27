"""`SupportFlowState`'s own shape, beyond what `test_supervisor.py`'s
higher-level tests already exercise.
"""

from src.application.supervisor import build_initial_state


def test_state_carries_tools_called() -> None:
    state = build_initial_state("текст", "r1", "s1", "t1")
    assert state["tools_called"] == []
