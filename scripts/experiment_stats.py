"""Paired statistics for a prompt experiment: is the candidate's score
actually different from production's, or is this run-to-run noise?

The design is paired — the same cases run under both prompt versions — so
every test here is within-case. An unpaired test would discard exactly the
variance control the design provides.

No scipy or numpy. At n=8 and n=12 the permutation space is small enough
to enumerate exhaustively (2**8 = 256, 2**12 = 4096), so the p-value is
*exact* rather than sampled, and McNemar's exact form is a binomial test
on the discordant count. Adding a dependency to compute two numbers this
small would not be justified.

The honest framing these functions exist to support: at these sample sizes
only large effects are detectable. Report an effect size with its interval
and say what the set cannot detect — never a bare "improved".
"""

import random
from dataclasses import dataclass
from itertools import product
from math import comb
from statistics import mean, stdev


@dataclass(frozen=True)
class PairedResult:
    """Outcome of one paired comparison.

    Parameters
    ----------
    mean_difference : float
        Candidate minus production, averaged over cases. Positive means
        the candidate scored higher.
    confidence_interval : tuple of float
        Bootstrap percentile interval on `mean_difference`. **Spanning
        zero means the experiment did not demonstrate a difference**,
        whatever the point estimate looks like.
    p_value : float
        Exact two-sided permutation p-value over sign flips.
    n : int
    """

    mean_difference: float
    confidence_interval: tuple[float, float]
    p_value: float
    n: int

    @property
    def is_inconclusive(self) -> bool:
        """`True` when the interval spans zero — the usual outcome here.

        Exposed as a property so a report cannot quietly skip the check
        and present `mean_difference` as if it were a finding.
        """
        low, high = self.confidence_interval
        return low <= 0.0 <= high


def exact_permutation_p_value(differences: list[float]) -> float:
    """Two-sided exact p-value for a paired difference, by sign flipping.

    Parameters
    ----------
    differences : list of float
        Per-case candidate-minus-production scores.

    Returns
    -------
    float
        Proportion of the 2**n sign assignments whose mean absolute value
        is at least the observed one. Exact, not sampled.

    Notes
    -----
    Refuses above 20 cases rather than silently switching to sampling: a
    caller who grew the set deserves to know the guarantee changed, and a
    p-value labelled "exact" that quietly became an estimate is worse than
    an error.
    """
    n = len(differences)
    if n == 0:
        raise ValueError("no paired differences to test")
    if n > 20:
        raise ValueError(
            f"exhaustive enumeration refuses n={n} (2**{n} assignments); "
            "this function's exactness guarantee does not survive sampling"
        )

    observed = abs(mean(differences))
    at_least_as_extreme = sum(
        1
        for signs in product((1, -1), repeat=n)
        if abs(mean([s * d for s, d in zip(signs, differences)])) >= observed - 1e-12
    )
    return at_least_as_extreme / (2**n)


def bootstrap_interval(
    differences: list[float],
    *,
    confidence: float = 0.95,
    resamples: int = 10_000,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile bootstrap interval for the mean paired difference.

    Parameters
    ----------
    seed : int, default=0
        Fixed so a reported interval is reproducible. An interval that
        moves between runs of the same data cannot be cited in a report.
    """
    if not differences:
        raise ValueError("no paired differences to resample")

    rng = random.Random(seed)
    n = len(differences)
    means = sorted(
        mean([differences[rng.randrange(n)] for _ in range(n)])
        for _ in range(resamples)
    )
    tail = (1.0 - confidence) / 2.0
    return means[int(tail * resamples)], means[int((1.0 - tail) * resamples) - 1]


def compare_paired(
    production: list[float], candidate: list[float], *, confidence: float = 0.95
) -> PairedResult:
    """Compare two score vectors measured on the same cases, in order.

    Raises
    ------
    ValueError
        Lengths differ — that means the vectors are not actually paired,
        and averaging them anyway would produce a confident wrong answer.
    """
    if len(production) != len(candidate):
        raise ValueError(
            f"unpaired inputs: {len(production)} production vs "
            f"{len(candidate)} candidate scores"
        )

    differences = [c - p for p, c in zip(production, candidate)]
    return PairedResult(
        mean_difference=mean(differences),
        confidence_interval=bootstrap_interval(differences, confidence=confidence),
        p_value=exact_permutation_p_value(differences),
        n=len(differences),
    )


def mcnemar_exact(
    production_correct: list[bool], candidate_correct: list[bool]
) -> float:
    """Exact two-sided McNemar p-value for paired binary outcomes.

    Parameters
    ----------
    production_correct, candidate_correct : list of bool
        Per-case correctness under each version, in the same case order.

    Returns
    -------
    float
        Two-sided binomial p-value on the discordant pairs. `1.0` when
        there are none.

    Notes
    -----
    Only discordant pairs carry information: a case both versions get
    right says nothing about which is better, and counting it as agreement
    in favour of the change is the classic way to manufacture a result.
    """
    if len(production_correct) != len(candidate_correct):
        raise ValueError("unpaired inputs")

    candidate_only = sum(
        1 for p, c in zip(production_correct, candidate_correct) if c and not p
    )
    production_only = sum(
        1 for p, c in zip(production_correct, candidate_correct) if p and not c
    )
    discordant = candidate_only + production_only
    if discordant == 0:
        return 1.0

    smaller = min(candidate_only, production_only)
    tail = sum(comb(discordant, k) for k in range(smaller + 1)) / (2**discordant)
    return min(1.0, 2.0 * tail)


def judge_noise_floor(repeat_run_means: list[float]) -> float:
    """Spread across repeat runs of the *same* prompt version.

    An effect smaller than this is not evidence of anything — it is the
    judge's own non-determinism. Report it beside any between-version
    difference; a chart showing only the difference hides the floor it
    has to clear.
    """
    if len(repeat_run_means) < 2:
        raise ValueError("need at least two runs of the same version")
    return stdev(repeat_run_means)
