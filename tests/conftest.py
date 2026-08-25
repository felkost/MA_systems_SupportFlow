"""Global test isolation: `pytest --cov=src` must never make a live
Langfuse call (README's Gate section — Langfuse joins the existing
Silpo MCP/Tavily/OpenRouter/Telegram list this promise already covers).

Found live during Stage 4 Wave A's own observability smoke test: with
`TRACING_ENABLED=true` genuinely set in the real `.env` (needed for that
live check), every test that never mocks `get_langfuse_client()` was
silently sending real spans to the real Langfuse project — one test run's
traffic included a real trace under the shared placeholder trace id
`"0123456789abcdef0123456789abcdef"` used across several test files. This
autouse fixture forces `settings.tracing_enabled` back to `False` for
every test regardless of the real `.env`'s value, so the test suite's
hermeticity never again depends on remembering to flip a `.env` flag back.
Individual tests (e.g. `tests/infrastructure/test_observability.py`) that
need to exercise the `True` path still can — they monkeypatch it
themselves, after this fixture's own baseline runs.
"""

import pytest

from src.infrastructure import observability


@pytest.fixture(autouse=True)
def _tracing_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(observability.settings, "tracing_enabled", False)
    monkeypatch.setattr(observability, "_client", None)
    monkeypatch.setattr(observability, "_configured", False)
