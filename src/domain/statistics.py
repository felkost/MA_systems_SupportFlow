"""Distribution-free interval estimation, stdlib only.

Lives in `domain` rather than beside its first caller in `scripts/`
because `infrastructure` needs it too — the quality panel reports an
interval next to every mean — and `src` may never import from `scripts`.
No scipy: one dependency for one percentile is not worth carrying.
"""

import random


def bootstrap_interval(
    values: list[float],
    *,
    confidence: float = 0.95,
    resamples: int = 10_000,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile bootstrap interval for the mean of a sample.

    Parameters
    ----------
    values : list of float
        A sample — raw scores, or the paired differences between two
        conditions. Both are means of a sample, so the same resampling
        applies.
    confidence : float, default=0.95
    resamples : int, default=10000
    seed : int, default=0
        Fixed so a reported interval is reproducible. An interval that
        moves between runs of the same data cannot be cited in a report.

    Returns
    -------
    tuple of (float, float)
        Lower and upper bound.

    Raises
    ------
    ValueError
        On an empty sample — there is no mean to put an interval around.

    Examples
    --------
    >>> low, high = bootstrap_interval([0.6, 0.8, 1.0])
    >>> low < 0.8 < high
    True
    >>> bootstrap_interval([0.6, 0.8, 1.0]) == bootstrap_interval([0.6, 0.8, 1.0])
    True

    Notes
    -----
    Resampling via `random.choices` instead of a per-draw `randrange`
    loop moves the C-level RNG call count around, so a given seed's
    interval shifts in the third decimal relative to the previous
    implementation. Reproducibility across calls with the same seed
    (the actual contract) is unaffected.
    """
    if not values:
        raise ValueError("no values to resample")

    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(resamples))
    tail = (1.0 - confidence) / 2.0
    return means[int(tail * resamples)], means[int((1.0 - tail) * resamples) - 1]
