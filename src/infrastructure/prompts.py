"""Fetch a versioned system prompt from Langfuse Prompt Management.
Never a hardcoded string — the Langfuse SDK's own
prompt cache absorbs a transient outage; only a cold-cache failure (no
cached copy at all) is allowed to raise.

Confirmed against the installed `langfuse==4.14.4` SDK
(`Langfuse.get_prompt` signature; `TextPromptClient.prompt`/`.version`/
`.compile`), not assumed from documentation.
"""

from typing import Any

from langfuse import Langfuse

from src.infrastructure.observability import get_langfuse_client
from src.kernel.settings import settings

# A stale cached prompt is acceptable; only a cold-cache fetch (no cache
# at all yet) is fatal. 5 minutes bounds how
# long a Langfuse outage can go undetected while still serving requests.
_CACHE_TTL_SECONDS = 300
_FETCH_TIMEOUT_SECONDS = 5

_bare_client: Langfuse | None = None


def _get_client() -> Langfuse:
    """The process's traced client if tracing is configured, otherwise a
    bare, untraced client of its own — prompt-fetching must keep working
    independent of tracing state. Driven by `get_langfuse_client()`'s
    actual return value, never by
    re-reading `settings.tracing_enabled` directly: a process where
    `configure_tracing()` was never called (e.g. a topology mistake) would
    otherwise misread "tracing enabled" as true, skip this fallback, and
    call `.get_prompt()` on `None`.
    """
    client = get_langfuse_client()
    if client is not None:
        return client
    global _bare_client
    if _bare_client is None:
        _bare_client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_base_url,
        )
    return _bare_client


def get_prompt_client(name: str, label: str = "production") -> Any:
    """Fetch the raw `TextPromptClient`/`ChatPromptClient` object — needed
    where a caller must attach the prompt to a Langfuse generation span
    (`start_as_current_observation(..., prompt=<PromptClient>)`) rather
    than just read its text/version. `get_prompt` below is a thin
    wrapper over this for callers that only need the two plain values.

    Parameters
    ----------
    name : str
    label : str, default="production"

    Returns
    -------
    TextPromptClient or ChatPromptClient

    Raises
    ------
    Exception
        Same cold-cache-fetch-failure contract as `get_prompt`.
    """
    client = _get_client()
    return client.get_prompt(
        name,
        label=label,
        cache_ttl_seconds=_CACHE_TTL_SECONDS,
        fetch_timeout_seconds=_FETCH_TIMEOUT_SECONDS,
    )


def get_prompt(name: str, label: str = "production") -> tuple[str, int]:
    """Fetch a prompt's text and its resolved version.

    Parameters
    ----------
    name : str
        Langfuse prompt name (e.g. `"supportflow/router"`).
    label : str, default="production"
        Mutable — this is why the *resolved* version is returned rather
        than the caller re-deriving it from the label.

    Returns
    -------
    (text, version) : tuple[str, int]

    Raises
    ------
    Exception
        Whatever the Langfuse SDK raises on a cold-cache fetch failure
        (no network and no prior cached copy) — propagated unchanged;
        never substitute a hardcoded string.
    """
    prompt_client = get_prompt_client(name, label)
    return prompt_client.prompt, prompt_client.version
