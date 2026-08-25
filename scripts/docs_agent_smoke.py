"""Manual, live smoke check for Docs Agent: real knowledge-base retrieval,
a real Silpo MCP OAuth+bootstrap+search call chain, and a real OpenRouter
LLM call — end to end, in-process (no A2A hop; that plumbing is already
proven by `tests/infrastructure/test_docs_client.py`'s ASGI round trip).

`pytest --cov=src` never touches the live Silpo MCP account
(docs/decisions.md #21) — this script is the counterpart, run manually by
the author after the first phone+OTP login has produced a cached token
(`.venv/Scripts/python scripts/probe_silpo_mcp.py`, or any successful
prior run of Docs Agent's own process):

    .venv/Scripts/python scripts/docs_agent_smoke.py "Чи є у вас безлактозне молоко?"
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.application.docs_agent import run_docs_agent  # noqa: E402


async def main(query: str) -> None:
    result = await run_docs_agent(query)
    print("ANSWER:", result.response.answer)
    print("CONFIDENCE:", result.response.confidence)
    print("N_SOURCES:", len(result.response.sources))
    for source in result.response.sources:
        print("  -", source.ref, "|", source.retrieved_at)
    print("RETRIEVAL_CONTEXT (first 200 chars each):")
    for chunk in result.retrieval_context:
        print("  -", chunk[:200].replace("\n", " "))


if __name__ == "__main__":
    query_arg = sys.argv[1] if len(sys.argv) > 1 else "Чи є у вас безлактозне молоко?"
    asyncio.run(main(query_arg))
