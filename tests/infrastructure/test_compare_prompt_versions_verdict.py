"""`_print_verdict`'s significance gate.

Regression test for a real bug found live 2026-08-27: the Router branch's
verdict checked `discordant_pairs == 0` instead of the p-value, so a
single discordant pair out of 12 (McNemar p=1.0 exactly — zero evidence)
printed "a difference was measured" instead of "inconclusive".
"""

import sys
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.compare_prompt_versions import _print_verdict  # noqa: E402


def _captured(summary: dict) -> str:
    buf = StringIO()
    real_stdout, sys.stdout = sys.stdout, buf
    try:
        _print_verdict(summary)
    finally:
        sys.stdout = real_stdout
    return buf.getvalue()


def test_one_discordant_pair_is_inconclusive_despite_nonzero_count() -> None:
    """The exact bug: discordant_pairs=1 must NOT read as a difference —
    McNemar's own p-value for one flip is 1.0, the least significant it
    gets.
    """
    summary = {"discordant_pairs": 1, "p_value": 1.0}

    assert "INCONCLUSIVE" in _captured(summary)


def test_a_low_p_value_reads_as_a_measured_difference() -> None:
    summary = {"discordant_pairs": 6, "p_value": 0.01}

    assert "A difference was measured" in _captured(summary)


def test_missing_p_value_defaults_to_inconclusive() -> None:
    # No p-value present must never be silently treated as significant.
    assert "INCONCLUSIVE" in _captured({})
