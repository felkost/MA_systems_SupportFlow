"""`experiment_tags` and `tag_trace`'s failure guard.

`tag_trace` is exercised here rather than in a Langfuse integration test
because the behaviour that matters is what happens when the SDK's private
method breaks — reproducible only with an injected failure.
"""

from typing import Any

import pytest

from src.infrastructure import observability


def test_no_experiment_configured_yields_no_tags(monkeypatch: Any) -> None:
    # The load-bearing case: a non-empty default would retroactively label
    # ordinary baseline traffic as belonging to an experiment.
    monkeypatch.setattr(observability.settings, "experiment", "")
    monkeypatch.setattr(observability.settings, "experiment_started_at", "")

    assert observability.experiment_tags() == []


def test_name_without_date_yields_one_tag(monkeypatch: Any) -> None:
    monkeypatch.setattr(observability.settings, "experiment", "docs-fewshot-v1")
    monkeypatch.setattr(observability.settings, "experiment_started_at", "")

    assert observability.experiment_tags() == ["experiment:docs-fewshot-v1"]


def test_name_and_date_yield_both_tags(monkeypatch: Any) -> None:
    monkeypatch.setattr(observability.settings, "experiment", "docs-fewshot-v1")
    monkeypatch.setattr(observability.settings, "experiment_started_at", "2026-08-27")

    assert observability.experiment_tags() == [
        "experiment:docs-fewshot-v1",
        "started:2026-08-27",
    ]


def test_tag_trace_never_propagates_an_sdk_failure(
    monkeypatch: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A broken private SDK method must not reach the caller.

    `tag_trace` runs on the live `/chat` path, so a raised exception here
    would turn every customer request into a 500 over a lost tag.
    """

    class _BrokenClient:
        def _create_trace_tags_via_ingestion(self, **_kwargs: Any) -> None:
            raise AttributeError("renamed in a future SDK release")

    monkeypatch.setattr(observability, "get_langfuse_client", lambda: _BrokenClient())

    observability.tag_trace("0" * 32, ["experiment:x"])

    assert "trace tagging failed" in caplog.text
