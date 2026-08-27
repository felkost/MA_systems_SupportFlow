"""`load_agent_config` against the real `config/models.yaml` — this is the
file every agent's model, timeout and threshold come from, so parsing it
is worth a real test, not only a monkeypatched one.
"""

from pathlib import Path

import pytest
import yaml

from src.kernel.settings import Settings, load_agent_config


def test_tracing_enabled_defaults_to_false() -> None:
    # Checks the field's declared default, not `Settings()`'s live value —
    # `Settings()` always reads the real `.env`, which legitimately sets
    # `TRACING_ENABLED=true` during a live observability smoke test
    # (scripts/observability_smoke.py); this test's own point is the
    # class-level default, not whatever the real file currently says.
    assert Settings.model_fields["tracing_enabled"].default is False


def test_load_agent_config_reads_the_real_models_yaml() -> None:
    config = load_agent_config("router")
    assert config.model  # no longer the "«model-scout»" placeholder
    assert not config.model.startswith("«")
    assert config.timeout_seconds == 10
    assert config.confidence_threshold is None
    assert config.max_retries == 1  # default


def test_load_agent_config_reads_every_declared_role() -> None:
    for role in ("router", "docs", "web_search", "escalation", "supervisor"):
        config = load_agent_config(role)  # type: ignore[arg-type]
        assert config.model


def test_load_agent_config_raises_on_missing_role(tmp_path: Path) -> None:
    fake_config = tmp_path / "models.yaml"
    fake_config.write_text(
        yaml.safe_dump(
            {
                "router": {
                    "model": "x",
                    "temperature": 0,
                    "max_tokens": 1,
                    "timeout_seconds": 1,
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(KeyError, match="docs"):
        load_agent_config("docs", path=fake_config)  # type: ignore[arg-type]
