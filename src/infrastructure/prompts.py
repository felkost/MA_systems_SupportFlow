"""Fetch a versioned system prompt from Langfuse Prompt Management
(docs/decisions.md #13). Never a hardcoded string — the Langfuse SDK's own
prompt cache absorbs a transient outage; only a cold-cache failure (no
cached copy at all) is allowed to raise.

Confirmed against the installed `langfuse==4.14.4` SDK
(`Langfuse.get_prompt` signature; `TextPromptClient.prompt`/`.version`/
`.compile`), not assumed from documentation.
"""

from langfuse import Langfuse

from src.kernel.settings import settings

# docs/decisions.md #13: a stale cached prompt is acceptable; only a
# cold-cache fetch (no cache at all yet) is fatal. 5 minutes bounds how
# long a Langfuse outage can go undetected while still serving requests.
_CACHE_TTL_SECONDS = 300
_FETCH_TIMEOUT_SECONDS = 5

_client: Langfuse | None = None


def _get_client() -> Langfuse:
    global _client
    if _client is None:
        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_base_url,
        )
    return _client


def get_prompt(name: str, label: str = "production") -> tuple[str, int]:
    """Fetch a prompt's text and its resolved version.

    Parameters
    ----------
    name : str
        Langfuse prompt name (e.g. `"supportflow/router"`).
    label : str, default="production"
        Mutable — docs/decisions.md #13 is why the *resolved* version is
        returned rather than the caller re-deriving it from the label.

    Returns
    -------
    (text, version) : tuple[str, int]

    Raises
    ------
    Exception
        Whatever the Langfuse SDK raises on a cold-cache fetch failure
        (no network and no prior cached copy) — propagated unchanged, per
        docs/decisions.md #13: never substitute a hardcoded string.
    """
    client = _get_client()
    prompt_client = client.get_prompt(
        name,
        label=label,
        cache_ttl_seconds=_CACHE_TTL_SECONDS,
        fetch_timeout_seconds=_FETCH_TIMEOUT_SECONDS,
    )
    return prompt_client.prompt, prompt_client.version
