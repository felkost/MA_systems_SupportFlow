"""`scripts/experiment_stats.py` — the tests that matter are the ones
asserting the functions refuse to manufacture a result.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.experiment_stats import (  # noqa: E402
    compare_paired,
    exact_permutation_p_value,
    judge_noise_floor,
    mcnemar_exact,
)


def test_identical_scores_are_never_significant() -> None:
    result = compare_paired([0.5] * 8, [0.5] * 8)

    assert result.mean_difference == 0.0
    assert result.p_value == 1.0
    assert result.is_inconclusive


def test_noisy_small_improvement_stays_inconclusive() -> None:
    """The realistic case, and the one the report must not overclaim.

    A candidate that wins on some cases and loses on others at n=8 cannot
    be distinguished from judge noise.
    """
    production = [0.60, 0.70, 0.55, 0.80, 0.65, 0.75, 0.50, 0.70]
    candidate = [0.65, 0.68, 0.60, 0.78, 0.70, 0.72, 0.55, 0.74]

    result = compare_paired(production, candidate)

    assert result.mean_difference > 0
    assert result.is_inconclusive


def test_a_consistent_large_effect_is_detected() -> None:
    # Every case improves by a wide margin — the only kind of effect n=8
    # can actually resolve.
    result = compare_paired([0.2] * 8, [0.9] * 8)

    assert not result.is_inconclusive
    assert result.p_value < 0.01


def test_permutation_p_value_is_exact_not_sampled() -> None:
    # All-positive differences: only the all-plus and all-minus sign
    # assignments reach the observed mean, so p is exactly 2/2**n.
    assert exact_permutation_p_value([0.1] * 4) == pytest.approx(2 / 16)


def test_permutation_refuses_to_silently_become_an_estimate() -> None:
    with pytest.raises(ValueError, match="exactness guarantee"):
        exact_permutation_p_value([0.1] * 21)


def test_unpaired_inputs_are_rejected() -> None:
    # Averaging mismatched vectors would give a confident wrong answer.
    with pytest.raises(ValueError, match="unpaired"):
        compare_paired([0.1, 0.2], [0.1])


def test_mcnemar_ignores_cases_both_versions_get_right() -> None:
    """Concordant pairs carry no information, and one flip proves nothing.

    A single discordant pair yields exactly p=1.0 — that is the correct
    answer, not a degenerate one: flipping one case out of twelve is what
    a coin does. A test suite that expected "any change lowers p" would be
    demanding the function manufacture a result.
    """
    assert mcnemar_exact([True] * 12, [True] * 12) == 1.0
    assert mcnemar_exact([True] * 12, [False] + [True] * 11) == 1.0


def test_mcnemar_counts_only_discordant_pairs() -> None:
    # Same 3-vs-0 discordance, padded with cases both versions get right:
    # the padding must not dilute the p-value.
    without_padding = mcnemar_exact([False] * 3, [True] * 3)
    with_padding = mcnemar_exact([False] * 3 + [True] * 9, [True] * 12)

    assert without_padding == with_padding


def test_mcnemar_detects_a_one_sided_flip() -> None:
    production = [False] * 8 + [True] * 4
    candidate = [True] * 8 + [True] * 4

    assert mcnemar_exact(production, candidate) < 0.01


def test_judge_noise_floor_needs_more_than_one_run() -> None:
    with pytest.raises(ValueError, match="at least two runs"):
        judge_noise_floor([0.61])
