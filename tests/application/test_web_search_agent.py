"""`run_web_search` propagates a tool-unavailable failure without ever
reaching the model (task §7 step 6's escalation trigger) — the one path
testable without a live LLM call.
"""

import pytest

from src.application.web_search_agent import run_web_search
from src.infrastructure.web_search import SearchUnavailableError


def test_run_web_search_propagates_tool_unavailable_without_calling_model() -> None:
    def failing_search(_query: str):
        raise SearchUnavailableError("both providers down")

    with pytest.raises(SearchUnavailableError):
        run_web_search("Чи є у вас акції на хліб?", search_fn=failing_search)
