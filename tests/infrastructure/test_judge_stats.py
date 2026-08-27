"""`summarize`'s small-sample honesty and `live_quality`'s fail-closed
population filter — the two places where a wrong answer would put a
number under a caption it does not describe.
"""

import json
from typing import Any

import src.infrastructure.judge_stats as judge_stats


def test_summarize_empty_sample_reports_nothing() -> None:
    assert judge_stats.summarize([]) == {
        "n": 0,
        "mean": None,
        "std_dev": None,
        "ci": None,
    }


def test_summarize_single_value_has_no_spread() -> None:
    """One observation has no measured spread — `None`, not `0.0`, which
    would claim perfect consistency from a single data point.
    """
    result = judge_stats.summarize([0.62])

    assert result["n"] == 1
    assert result["mean"] == 0.62
    assert result["std_dev"] is None
    assert result["ci"] is None


def test_summarize_reports_mean_spread_and_range() -> None:
    result = judge_stats.summarize([0.6, 0.8, 1.0])

    assert result["n"] == 3
    assert result["mean"] == 0.8
    assert result["std_dev"] is not None
    low, high = result["ci"]
    assert low <= result["mean"] <= high


def test_live_quality_fails_closed_without_an_experiment_tag(
    monkeypatch: Any,
) -> None:
    """With no tag to filter by, the only available pool mixes live
    answers with offline script runs — reporting it as live quality is
    the exact error this guard exists to prevent.
    """
    monkeypatch.setattr(judge_stats, "experiment_tags", lambda: [])

    assert judge_stats.live_quality() == {
        "available": False,
        "reason": "no_experiment_configured",
    }


def test_live_quality_reports_unavailable_when_tracing_is_off(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(judge_stats, "experiment_tags", lambda: ["experiment:x"])
    monkeypatch.setattr(judge_stats, "get_langfuse_client", lambda: None)

    assert judge_stats.live_quality()["reason"] == "tracing_disabled"


def test_live_quality_filters_by_the_configured_experiment_tag(
    monkeypatch: Any,
) -> None:
    """The tag actually reaches Langfuse — without it the query returns
    the unfiltered, mixed-population pool.
    """
    calls: list[dict[str, Any]] = []

    class _Score:
        def __init__(self, value: float) -> None:
            self.value = value

    class _Scores:
        def get_many(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return type("R", (), {"data": [_Score(0.8), _Score(0.6)]})()

    client = type("C", (), {"api": type("A", (), {"scores": _Scores()})()})()
    monkeypatch.setattr(judge_stats, "experiment_tags", lambda: ["experiment:demo"])
    monkeypatch.setattr(judge_stats, "get_langfuse_client", lambda: client)

    result = judge_stats.live_quality(limit=5)

    assert all(c["trace_tags"] == ["experiment:demo"] for c in calls)
    assert all(c["limit"] == 5 for c in calls)
    assert [c["name"] for c in calls] == list(judge_stats.LIVE_SCORE_NAMES)
    assert result["available"] is True
    assert set(result["metrics"]) == set(judge_stats.LIVE_SCORE_NAMES)
    assert result["metrics"]["supportflow-answer-relevance"]["mean"] == 0.7


def test_live_quality_keeps_each_evaluator_separate(monkeypatch: Any) -> None:
    """Pooling the two rubrics would average incomparable populations —
    the defect that split them into separate rules to begin with.
    """
    by_name = {
        "supportflow-answer-relevance": [0.9, 0.7],
        "supportflow-escalation-quality": [0.4],
    }

    class _Scores:
        def get_many(self, **kwargs: Any) -> Any:
            scores = [type("S", (), {"value": v})() for v in by_name[kwargs["name"]]]
            return type("R", (), {"data": scores})()

    client = type("C", (), {"api": type("A", (), {"scores": _Scores()})()})()
    monkeypatch.setattr(judge_stats, "experiment_tags", lambda: ["experiment:demo"])
    monkeypatch.setattr(judge_stats, "get_langfuse_client", lambda: client)

    metrics = judge_stats.live_quality()["metrics"]

    assert metrics["supportflow-answer-relevance"]["n"] == 2
    assert metrics["supportflow-escalation-quality"]["n"] == 1
    assert metrics["supportflow-escalation-quality"]["mean"] == 0.4


def test_live_quality_survives_a_failed_read(monkeypatch: Any) -> None:
    """A measurement read must never take down the page it decorates."""

    class _Scores:
        def get_many(self, **kwargs: Any) -> Any:
            raise RuntimeError("langfuse unreachable")

    client = type("C", (), {"api": type("A", (), {"scores": _Scores()})()})()
    monkeypatch.setattr(judge_stats, "experiment_tags", lambda: ["experiment:demo"])
    monkeypatch.setattr(judge_stats, "get_langfuse_client", lambda: client)

    assert judge_stats.live_quality()["reason"] == "fetch_failed"


def test_live_deepeval_reports_when_no_batch_has_run(
    monkeypatch: Any, tmp_path: Any
) -> None:
    monkeypatch.setattr(judge_stats, "LIVE_EVAL_PATH", tmp_path / "absent.json")

    assert judge_stats.live_deepeval() == {
        "available": False,
        "reason": "no_batch_run_yet",
    }


def test_live_deepeval_summarizes_the_last_batch(
    monkeypatch: Any, tmp_path: Any
) -> None:
    """Same metrics as the golden baseline, so the two can be read
    against each other — the panel's only valid comparison.
    """
    path = tmp_path / "live_eval.json"
    path.write_text(
        json.dumps(
            {
                "measured_at": "2026-08-28T12:00:00+00:00",
                "cases": [
                    {
                        "trace_id": "t1",
                        "route": "docs",
                        "scores": {"Answer Relevancy": 0.8, "Privacy Safety": 1.0},
                    },
                    {
                        "trace_id": "t2",
                        "route": "docs",
                        "scores": {"Answer Relevancy": 1.0},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(judge_stats, "LIVE_EVAL_PATH", path)
    monkeypatch.setattr(judge_stats, "read_cases", lambda: [])

    result = judge_stats.live_deepeval()

    assert result["judge"] == "DeepEval"
    assert result["n_cases"] == 2
    assert result["metrics"]["Answer Relevancy"]["mean"] == 0.9
    assert result["metrics"]["Privacy Safety"]["std_dev"] is None
    assert result["metrics"]["Answer Relevancy"]["covers"] == 2
    assert result["pending"] == 0


def test_golden_baseline_names_its_judge_and_every_metric() -> None:
    """The reference lines come from a different instrument than the live
    judge; the payload has to say which, or the UI cannot label them.
    """
    result = judge_stats.golden_baseline()

    assert result["available"] is True
    assert result["judge"] == "DeepEval"
    assert "Answer Relevancy" in result["metrics"]
    for name, stats in result["metrics"].items():
        assert stats["n"] > 0, name
        assert 0.0 <= stats["mean"] <= 1.0, name


def test_golden_baseline_skips_metrics_a_case_did_not_measure() -> None:
    """A `null` score means the metric did not apply to that case.
    Counting it as zero would invent a failure the run never measured, so
    `n` is expected to differ between metrics.
    """
    metrics = judge_stats.golden_baseline()["metrics"]

    assert len({stats["n"] for stats in metrics.values()}) > 1
