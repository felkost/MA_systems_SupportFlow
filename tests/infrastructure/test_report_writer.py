"""`src.infrastructure.report_writer` — a run-scoped file write, tested
against `tmp_path` so no repository file is ever touched.
"""

import json
from pathlib import Path

from src.infrastructure.report_writer import write_escalation_report


def test_write_escalation_report_creates_session_scoped_file(tmp_path: Path) -> None:
    path = write_escalation_report(
        {"summary": "test case"},
        session_id="s1",
        request_id="r1",
        base_dir=tmp_path,
    )

    assert path == tmp_path / "s1" / "r1.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"summary": "test case"}


def test_write_escalation_report_creates_missing_directories(tmp_path: Path) -> None:
    base_dir = tmp_path / "does" / "not" / "exist"

    path = write_escalation_report(
        {"summary": "x"}, session_id="s2", request_id="r2", base_dir=base_dir
    )

    assert path.exists()


def test_write_escalation_report_preserves_ukrainian_text(tmp_path: Path) -> None:
    path = write_escalation_report(
        {"summary": "Клієнт скаржиться на алергію"},
        session_id="s3",
        request_id="r3",
        base_dir=tmp_path,
    )

    assert "Клієнт" in path.read_text(encoding="utf-8")
