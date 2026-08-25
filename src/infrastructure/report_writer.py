"""Escalation report file writer: one JSON file per request, under a
directory scoped to its session (docs/decisions.md #19 — never one
ever-appending path, since an automated run can produce many reports).
"""

import json
from pathlib import Path
from typing import Any

from src.kernel.settings import PROJECT_ROOT

REPORTS_DIR = PROJECT_ROOT / "output" / "escalations"


def write_escalation_report(
    payload: dict[str, Any],
    *,
    session_id: str,
    request_id: str,
    base_dir: Path = REPORTS_DIR,
) -> Path:
    """Write `payload` as JSON to `{base_dir}/{session_id}/{request_id}.json`.

    Parameters
    ----------
    payload : dict[str, Any]
        JSON-serialisable — the caller is responsible for that (e.g.
        `model_dump(mode="json")` on a Pydantic model).
    session_id, request_id : str
    base_dir : Path, default=REPORTS_DIR

    Returns
    -------
    Path
        The file actually written.
    """
    session_dir = base_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"{request_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
