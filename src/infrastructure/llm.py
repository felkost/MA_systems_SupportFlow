"""OpenRouter chat-model factory. Every LLM role routes through OpenRouter
— no direct-provider path exists (`.env.example`).
"""

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from src.kernel.settings import AgentRole, load_agent_config, settings

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def get_chat_model(
    role: AgentRole, timeout_override: float | None = None
) -> ChatOpenAI:
    """Build a chat model for one agent role, configured from
    `config/models.yaml`.

    Parameters
    ----------
    role : {"router", "docs", "web_search", "escalation", "supervisor"}
    timeout_override : float or None
        When set, replaces `config/models.yaml`'s `timeout_seconds` — used
        by `src.infrastructure.acp.call_router` to bound the remaining
        time on `AcpEnvelope.deadline` rather than the full per-call
        timeout.

    Returns
    -------
    ChatOpenAI

    Notes
    -----
    `max_retries=0`: retry policy is `application` layer's job — a
    client-level retry here would hide a failure from the fail-closed
    logic that must count it.
    """
    config = load_agent_config(role)
    timeout = (
        timeout_override if timeout_override is not None else config.timeout_seconds
    )
    return ChatOpenAI(
        model=config.model,
        api_key=SecretStr(settings.openrouter_api_key),
        base_url=_OPENROUTER_BASE_URL,
        temperature=config.temperature,
        # ChatOpenAI's `max_tokens` field is an alias for the OpenAI API's
        # current `max_completion_tokens` param — `max_tokens` itself is
        # deprecated upstream (langchain_openai.chat_models.base).
        max_completion_tokens=config.max_tokens,
        timeout=timeout,
        max_retries=0,
    )
