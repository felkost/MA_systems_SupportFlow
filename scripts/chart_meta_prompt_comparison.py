"""Regenerable SVG chart of Docs Agent's meta-prompting comparison —
per-case Support Resolution Quality, `production` vs `candidate`,
min-max range (n=3 runs/case; too few repeats for a real confidence
interval, so this chart shows the observed range, not a fabricated CI —
say so on the chart itself, not just here).

No plotting library installed in this project's venv (checked before
adding one, ponytail rung 5) — draws plain SVG directly from
`output/meta_prompt_docs_comparison.json`, regenerated from data, never
hand-edited.

    .venv/Scripts/python scripts/chart_meta_prompt_comparison.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kernel.settings import PROJECT_ROOT  # noqa: E402

INPUT_PATH = Path(PROJECT_ROOT) / "output" / "meta_prompt_docs_comparison.json"
OUTPUT_PATH = (
    Path(PROJECT_ROOT) / "docs" / "stage-reports" / "stage4-wave-b-meta-prompt.svg"
)

_PRODUCTION_COLOR = "#8a8a8a"
_CANDIDATE_COLOR = "#2563eb"
_THRESHOLD_COLOR = "#c0392b"
_THRESHOLD = 0.70

_BAR_H = 14
_ROW_H = 44
_LEFT_MARGIN = 220
_CHART_W = 520
_TOP_MARGIN = 70
_BOTTOM_MARGIN = 60


def _case_stats(runs: list[dict]) -> tuple[float, float, float]:
    scores = [r["score"] for r in runs]
    return sum(scores) / len(scores), min(scores), max(scores)


def main() -> None:
    data = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    case_ids = list(data["production"].keys())
    n_runs = len(next(iter(data["production"].values())))

    height = _TOP_MARGIN + len(case_ids) * _ROW_H * 2 + _BOTTOM_MARGIN
    width = _LEFT_MARGIN + _CHART_W + 40

    def x(score: float) -> float:
        return _LEFT_MARGIN + score * _CHART_W

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'font-family="sans-serif" font-size="12">',
        f'<rect width="{width}" height="{height}" fill="white"/>',
        f'<text x="{width / 2}" y="24" text-anchor="middle" font-size="16" '
        f'font-weight="bold">Docs Agent meta-prompting: Support Resolution '
        f"Quality per case</text>",
        f'<text x="{width / 2}" y="42" text-anchor="middle" font-size="12" '
        f'fill="#555">production vs candidate, n={n_runs} runs/case, bars show '
        f"min–max range (too few runs for a real CI) — "
        f"evals/docs_optimization_set.json, 2026-08-26</text>",
    ]

    # 0-1 axis, never truncated.
    axis_y = height - _BOTTOM_MARGIN + 20
    svg.append(
        f'<line x1="{x(0)}" y1="{axis_y}" x2="{x(1)}" y2="{axis_y}" stroke="black"/>'
    )
    for tick in (0, 0.25, 0.5, 0.75, 1.0):
        svg.append(
            f'<line x1="{x(tick)}" y1="{axis_y}" x2="{x(tick)}" '
            f'y2="{axis_y + 5}" stroke="black"/>'
        )
        svg.append(
            f'<text x="{x(tick)}" y="{axis_y + 18}" text-anchor="middle">{tick}</text>'
        )
    svg.append(
        f'<line x1="{x(_THRESHOLD)}" y1="{_TOP_MARGIN - 10}" '
        f'x2="{x(_THRESHOLD)}" y2="{axis_y}" stroke="{_THRESHOLD_COLOR}" '
        f'stroke-dasharray="4,3"/>'
    )
    svg.append(
        f'<text x="{x(_THRESHOLD) + 4}" y="{_TOP_MARGIN - 14}" '
        f'fill="{_THRESHOLD_COLOR}">threshold 0.70</text>'
    )

    y = _TOP_MARGIN
    for case_id in case_ids:
        p_mean, p_min, p_max = _case_stats(data["production"][case_id])
        c_mean, c_min, c_max = _case_stats(data["candidate"][case_id])

        svg.append(
            f'<text x="{_LEFT_MARGIN - 10}" y="{y + _ROW_H / 2 + 4}" '
            f'text-anchor="end" font-size="11">{case_id}</text>'
        )

        for label, mean, lo, hi, color, dy in (
            ("production", p_mean, p_min, p_max, _PRODUCTION_COLOR, 0),
            ("candidate", c_mean, c_min, c_max, _CANDIDATE_COLOR, _ROW_H),
        ):
            row_y = y + dy + (_ROW_H - _BAR_H) / 2
            svg.append(
                f'<line x1="{x(lo)}" y1="{row_y + _BAR_H / 2}" '
                f'x2="{x(hi)}" y2="{row_y + _BAR_H / 2}" stroke="{color}" '
                f'stroke-width="2"/>'
            )
            svg.append(
                f'<circle cx="{x(mean)}" cy="{row_y + _BAR_H / 2}" r="5" '
                f'fill="{color}"/>'
            )
            svg.append(
                f'<text x="{x(hi) + 8}" y="{row_y + _BAR_H / 2 + 4}" '
                f'font-size="10" fill="{color}">{label} '
                f"({mean:.2f})</text>"
            )
        y += _ROW_H * 2

    svg.append("</svg>")
    OUTPUT_PATH.write_text("\n".join(svg), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
