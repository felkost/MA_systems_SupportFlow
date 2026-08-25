"""Environment configuration and the `config/models.yaml` loader.

`kernel` holds settings, paths and constants only — never business logic —
so that `domain` can read a constant without importing `infra`
(docs/decisions.md #9).
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MODELS_CONFIG_PATH = PROJECT_ROOT / "config" / "models.yaml"

AgentRole = Literal["router", "docs", "web_search", "escalation", "supervisor"]


class Settings(BaseSettings):
    """Process environment, read once at import time.

    Parameters
    ----------
    openrouter_api_key : str
        Every LLM role routes through OpenRouter; no direct-provider path
        exists (.env.example).
    tavily_api_key : str, default=""
        Web Search Agent's primary provider (task §3); `ddgs` (DuckDuckGo)
        needs no key and is the fallback (docs/decisions.md #20).
    silpo_mcp_url : str, default="https://mcp.silpo.ua/mcp"
        Confirmed live endpoint (docs/decisions.md #3).
    langfuse_public_key, langfuse_secret_key : str, default=""
        Empty means tracing/prompt-fetch against Langfuse is not configured.
        `docs/decisions.md` #13 makes a cold-cache Langfuse prompt fetch a
        fatal error, not a silent fallback — an empty key here is exactly
        that cold-cache case for `src.infrastructure.prompts`.
    langfuse_base_url : str, default="https://cloud.langfuse.com"
        No self-hosted Langfuse stack in this project (docs/decisions.md
        references the ~5.5 GB self-hosted v3 memory cost as the reason).
    telegram_bot_token, telegram_chat_id : str, default=""
        `docs/telegram_bot_setup.md` walks through obtaining both. Empty
        means Escalation's file-write-only path still works; a real send
        additionally requires `allow_real_send=True` and this `chat_id`
        matching the one actually sent to (docs/decisions.md #19, F17).
    bypass_hitl : bool, default=False
        Skips Escalation's interactive confirmation prompt. Independent of
        `allow_real_send` — bypassing the human confirmation does not, by
        itself, permit a real Telegram call (docs/decisions.md #19).
    allow_real_send : bool, default=False
        Permits an actual Telegram API call. `False` in every automated
        test/eval run; `True` only for the one-off
        `scripts/escalation_agent_smoke.py` live check.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openrouter_api_key: str = ""
    tavily_api_key: str = ""
    silpo_mcp_url: str = "https://mcp.silpo.ua/mcp"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    bypass_hitl: bool = False
    allow_real_send: bool = False


class AgentModelConfig(BaseModel):
    """One agent's row from `config/models.yaml` (task §8).

    Parameters
    ----------
    model : str
        OpenRouter model id.
    temperature : float
    max_tokens : int
    timeout_seconds : float
        Per-call bound enforced by `src.infrastructure.acp.call_router`
        (docs/decisions.md #19 — a `deadline` field nothing checks is worse
        than no field at all).
    confidence_threshold : float or None
        `None` for agents with no confidence output (Router, Escalation,
        Supervisor) — task §6 defines `confidence` only on `DocsResponse`
        and `WebSearchResponse`.
    max_retries : int, default=1
        docs/decisions.md #12: one repair retry before Router fails closed
        to Escalation. Applies uniformly; only Router's failure path is
        built in Stage 1.
    port : int or None, default=None
        docs/decisions.md #26: set only for `docs` and `web_search`, the
        two agents running as separate A2A server processes (Stage 2).
        `None` for every in-process agent (Router, Escalation, Supervisor).
    """

    model: str
    temperature: float
    max_tokens: int
    timeout_seconds: float = Field(gt=0)
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    max_retries: int = Field(default=1, ge=0)
    port: int | None = Field(default=None, gt=0, lt=65536)


def load_agent_config(
    role: AgentRole, path: Path = MODELS_CONFIG_PATH
) -> AgentModelConfig:
    """Load one agent's row out of `config/models.yaml`.

    Parameters
    ----------
    role : {"router", "docs", "web_search", "escalation", "supervisor"}
    path : Path, default=MODELS_CONFIG_PATH

    Returns
    -------
    AgentModelConfig

    Raises
    ------
    KeyError
        `role` has no entry in the file — fails loudly rather than
        defaulting, since a missing agent config is a deployment error, not
        a runtime condition to route around.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if role not in raw:
        raise KeyError(f"config/models.yaml has no '{role}' entry")
    return AgentModelConfig.model_validate(raw[role])


settings = Settings()
