"""Docs Agent's retriever must be buildable before the first request —
the cold build costs ~72s (measured 2026-08-27) and, left inside request
handling, exceeds `config/models.yaml`'s `docs.timeout_seconds`.
"""

from typing import Any

import src.application.docs_agent as docs_agent


def test_warm_up_retriever_builds_once_and_is_reused(
    monkeypatch: Any,
) -> None:
    builds: list[int] = []

    def fake_build(_chunks: Any) -> str:
        builds.append(1)
        return "retriever"

    monkeypatch.setattr(docs_agent, "build_retriever", fake_build)
    monkeypatch.setattr(docs_agent, "load_knowledge_base", lambda: [])
    monkeypatch.setattr(docs_agent, "_retriever_singleton", None)

    docs_agent.warm_up_retriever()
    # A request arriving after warm-up must not pay the build cost again.
    assert docs_agent._get_retriever() == "retriever"
    assert len(builds) == 1
