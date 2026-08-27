"""Measure a `candidate` prompt against `production` on the same cases,
and report whether the difference survives the run-to-run noise.

Reuses the existing runners rather than reimplementing them:
`meta_prompt_docs._run_set` already scores the optimization set under a
given label, and `run_router_gate._run_once` already runs the held-out
classification set. This script only adds the second label, the pairing,
and the statistics.

Paired by construction: the same cases run under both labels, so every
comparison is within-case.

Costs real money — it is stated before anything runs, and the run waits
for a typed confirmation.

    .venv/Scripts/python scripts/compare_prompt_versions.py docs
    .venv/Scripts/python scripts/compare_prompt_versions.py router
"""

import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.experiment_stats import (  # noqa: E402
    compare_paired,
    judge_noise_floor,
    mcnemar_exact,
)
from src.kernel.settings import PROJECT_ROOT  # noqa: E402

_LABELS = ("production", "candidate")
_OUTPUT_DIR = Path(PROJECT_ROOT) / "output"


def _confirm(what: str, calls: str) -> None:
    print(f"\n{what}\nThis will make {calls}.")
    if input("Type 'run' to proceed: ").strip() != "run":
        print("Aborted — nothing was called.")
        raise SystemExit(1)


def _compare_docs() -> dict[str, Any]:
    from scripts.meta_prompt_docs import N_RUNS, _load_cases, _run_set

    n_cases = len(_load_cases())
    _confirm(
        f"Docs: {n_cases} cases x {N_RUNS} runs x 2 prompt labels.",
        f"{n_cases * N_RUNS * 2} Docs Agent calls and as many judge calls",
    )

    by_label = {label: _run_set(label) for label in _LABELS}
    case_ids = sorted(by_label["production"])

    # One number per case per label: the mean across repeat runs, so the
    # pairing is case-to-case rather than run-to-run.
    means = {
        label: [
            statistics.mean(r["score"] for r in by_label[label][cid])
            for cid in case_ids
        ]
        for label in _LABELS
    }
    result = compare_paired(means["production"], means["candidate"])

    # The floor the effect has to clear: how much the judge alone moves
    # between identical runs of the same prompt.
    per_run_production = [
        statistics.mean(by_label["production"][cid][i]["score"] for cid in case_ids)
        for i in range(N_RUNS)
    ]
    return {
        "experiment": "docs-fewshot",
        "n_cases": n_cases,
        "runs_per_version": N_RUNS,
        "production_mean": statistics.mean(means["production"]),
        "candidate_mean": statistics.mean(means["candidate"]),
        "mean_difference": result.mean_difference,
        "confidence_interval": list(result.confidence_interval),
        "p_value": result.p_value,
        "inconclusive": result.is_inconclusive,
        "judge_noise_floor": judge_noise_floor(per_run_production),
        "per_case": {
            cid: {label: means[label][i] for label in _LABELS}
            for i, cid in enumerate(case_ids)
        },
    }


def _compare_router() -> dict[str, Any]:
    from scripts.run_router_gate import RUNS, CASES_PATH, _load_env, _run_once

    _load_env()
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    _confirm(
        f"Router: {len(cases)} cases x {RUNS} runs x 2 prompt labels.",
        f"{len(cases) * RUNS * 2} Router calls (cheapest model, no judge)",
    )

    by_label = {
        label: [_run_once(cases, label) for _ in range(RUNS)] for label in _LABELS
    }
    # A case counts as correct only if every repeat run got it right —
    # a case the prompt answers correctly two times in three is not a
    # case it answers correctly.
    correct = {
        label: [
            all(run["cases"][i]["correct"] for run in by_label[label])
            for i in range(len(cases))
        ]
        for label in _LABELS
    }
    p_value = mcnemar_exact(correct["production"], correct["candidate"])
    discordant = sum(
        1 for p, c in zip(correct["production"], correct["candidate"]) if p != c
    )
    return {
        "experiment": "router-fewshot",
        "n_cases": len(cases),
        "runs_per_version": RUNS,
        "production_accuracy": sum(correct["production"]) / len(cases),
        "candidate_accuracy": sum(correct["candidate"]) / len(cases),
        "discordant_pairs": discordant,
        "p_value": p_value,
        "accuracy_by_run": {
            label: [run["accuracy"] for run in by_label[label]] for label in _LABELS
        },
    }


_SIGNIFICANCE_ALPHA = 0.05


def _print_verdict(summary: dict[str, Any]) -> None:
    print("\n== Result ==")
    for key, value in summary.items():
        if key != "per_case":
            print(f"  {key}: {value}")

    # Bug fixed 2026-08-27: this used to check `discordant_pairs == 0` for
    # the Router branch, which is wrong — one discordant pair out of 12
    # still yields p=1.0 exactly (confirmed by
    # `tests/evaluation/test_experiment_stats.py`'s own McNemar tests),
    # and printed "a difference was measured" for a result with zero
    # statistical evidence behind it. The p-value is the only correct
    # gate for both branches.
    p_value = summary.get("p_value")
    is_significant = p_value is not None and p_value < _SIGNIFICANCE_ALPHA
    if summary.get("inconclusive") or not is_significant:
        print(
            "\nINCONCLUSIVE. The difference does not clear the noise at this "
            "sample size. That is a result: report it, and leave the prompt "
            "on zero-shot."
        )
    else:
        print(
            "\nA difference was measured. Before promoting, check it also "
            "exceeds the judge noise floor above — statistical significance "
            "on a small set is not the same as a difference that matters."
        )


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    if len(sys.argv) != 2 or sys.argv[1] not in {"docs", "router"}:
        print("usage: compare_prompt_versions.py {docs|router}")
        raise SystemExit(2)

    summary = _compare_docs() if sys.argv[1] == "docs" else _compare_router()
    summary["timestamp_utc"] = datetime.now(timezone.utc).isoformat()

    _OUTPUT_DIR.mkdir(exist_ok=True)
    path = _OUTPUT_DIR / f"{summary['experiment']}-comparison.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    _print_verdict(summary)
    print(f"\nWritten to {path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
