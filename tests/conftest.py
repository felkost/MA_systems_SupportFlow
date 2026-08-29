"""Global test isolation: `pytest --cov=src` must never make a live
Langfuse call (README's Gate section — Langfuse joins the existing
Silpo MCP/Tavily/OpenRouter/Telegram list this promise already covers).

Found live during an observability smoke test: with
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

`@pytest.mark.eval` tests are exempt: they are never collected by the default
`pytest --cov=src` gate (`pyproject.toml`'s own `addopts = "-m 'not eval'"`
already excludes them), so this exemption cannot affect that promise —
but forcing tracing off unconditionally here also silently disabled it
for the one deliberate run that most needs it, the full golden-dataset
gate (`deepeval test run tests/test_golden_dataset.py -m eval`), which
must be traced end-to-end like every other live run.
"""

import pytest

from src.application import graph_nodes
from src.infrastructure import live_case_log, observability


@pytest.fixture(autouse=True)
def _tracing_disabled_by_default(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    if request.node.get_closest_marker("eval") is not None:
        return
    monkeypatch.setattr(observability.settings, "tracing_enabled", False)
    monkeypatch.setattr(observability, "_client", None)
    monkeypatch.setattr(observability, "_configured", False)


@pytest.fixture(autouse=True)
def _live_case_log_redirected_to_tmp(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the test suite out of the real recorded-cases file.

    Found the same way the tracing fixture above was: a run of the API
    tests left `masked_text="q"`/`answer="ok"` — `test_api.py`'s own
    fixture values — sitting in `output/live_cases.jsonl` next to real
    recorded requests, where the offline batch scorer would have graded
    them as if a customer had asked them.

    Redirected for every test rather than patched in the one module that
    posts to `/chat`: any future test that reaches the endpoint would
    otherwise reintroduce the same pollution silently, and nothing about
    the failure is visible from inside the test that causes it.

    No `eval` exemption, unlike the tracing fixture: writing test
    material into the live measurement file is never wanted, and the
    golden-dataset gate calls the supervisor directly rather than through
    the endpoint that records.
    """
    monkeypatch.setattr(live_case_log, "LIVE_CASES_PATH", tmp_path / "live_cases.jsonl")


@pytest.fixture(autouse=True)
def _checkpointer_reset_between_tests() -> None:
    """`graph_nodes._checkpointer` is a module-level, process-lifetime
    `InMemorySaver` (docs/decisions.md #77) — without this, every test
    that calls `supervisor.handle_request` with the same literal
    `session_id` (most of `tests/application/test_supervisor.py` uses
    `"s1"`) would share one checkpointed thread's `errors`/
    `conversation_history` with every other such test, making a currently
    order-independent suite order-dependent.
    """
    graph_nodes.reset_checkpointer()
