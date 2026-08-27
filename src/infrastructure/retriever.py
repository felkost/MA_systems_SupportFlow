"""Hybrid retrieval over the internal knowledge base — Chroma (semantic)
+ BM25 (keyword) combined via `EnsembleRetriever`.

Heavy ML imports (`langchain_huggingface`'s embedding model, `chromadb`)
stay inside `build_retriever()`, never at module top level, and the
retriever itself is built once, lazily, on Docs Agent's first use — not
at any process's startup. Confirmed against the installed packages:
`EnsembleRetriever` lives in `langchain_classic.retrievers.ensemble` (not
`langchain.retrievers` — that module doesn't exist in `langchain==1.3.16`'s
layout), `BM25Retriever` still comes from `langchain_community.retrievers`.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.kernel.settings import PROJECT_ROOT

KNOWLEDGE_BASE_DIR = PROJECT_ROOT / "data" / "knowledge_base"


@dataclass(frozen=True)
class KnowledgeChunk:
    """One retrievable unit of the internal knowledge base. Every
    document carries a source, a retrieval date and a rule version.
    """

    id: str
    text: str
    source: str
    retrieved_at: datetime
    version: str


def _parse_retrieved_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _load_json(filename: str) -> list[dict[str, Any]]:
    path = KNOWLEDGE_BASE_DIR / filename
    return list(json.loads(path.read_text(encoding="utf-8")))


def _load_faq() -> list[KnowledgeChunk]:
    return [
        KnowledgeChunk(
            id=item["id"],
            text=f"{item['question']}\n{item['answer']}",
            source=item["source"],
            retrieved_at=_parse_retrieved_at(item["retrieved_at"]),
            version=item["version"],
        )
        for item in _load_json("faq.json")
    ]


def _load_services() -> list[KnowledgeChunk]:
    return [
        KnowledgeChunk(
            id=item["id"],
            text=f"{item['title']}\n{item['content']}",
            source=item["source"],
            retrieved_at=_parse_retrieved_at(item["retrieved_at"]),
            version=item["version"],
        )
        for item in _load_json("services.json")
    ]


def _load_dialogues() -> list[KnowledgeChunk]:
    return [
        KnowledgeChunk(
            id=item["id"],
            text=item["content"],
            source=item["source"],
            retrieved_at=_parse_retrieved_at(item["retrieved_at"]),
            version=item["version"],
        )
        for item in _load_json("dialogues.json")
    ]


def load_knowledge_base() -> list[KnowledgeChunk]:
    """All chunks from `data/knowledge_base/` — FAQ, service descriptions,
    example dialogues.
    """
    return _load_faq() + _load_services() + _load_dialogues()


def build_retriever(
    chunks: list[KnowledgeChunk], embedding_function: Any = None
) -> Any:
    """Build the hybrid Chroma+BM25 retriever over `chunks`.

    Parameters
    ----------
    chunks : list[KnowledgeChunk]
    embedding_function : langchain_core.embeddings.Embeddings, optional
        Injected for testing (a cheap deterministic fake, avoiding the
        real model download); production callers omit it to lazily load
        the real `sentence-transformers` model.

    Returns
    -------
    EnsembleRetriever
        `.invoke(query)` returns `list[Document]`; each `Document.metadata`
        carries `id`/`source`/`retrieved_at`/`version` from `KnowledgeChunk`.
    """
    from langchain_chroma import Chroma
    from langchain_classic.retrievers.ensemble import EnsembleRetriever
    from langchain_community.retrievers import BM25Retriever
    from langchain_core.documents import Document

    if embedding_function is None:
        from langchain_huggingface import HuggingFaceEmbeddings

        embedding_function = HuggingFaceEmbeddings(
            model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )

    documents = [
        Document(
            page_content=chunk.text,
            metadata={
                "id": chunk.id,
                "source": chunk.source,
                "retrieved_at": chunk.retrieved_at.isoformat(),
                "version": chunk.version,
            },
        )
        for chunk in chunks
    ]

    chroma_retriever = Chroma.from_documents(
        documents, embedding=embedding_function
    ).as_retriever(search_kwargs={"k": 4})
    bm25_retriever = BM25Retriever.from_documents(documents)
    bm25_retriever.k = 4

    return EnsembleRetriever(
        retrievers=[chroma_retriever, bm25_retriever], weights=[0.5, 0.5]
    )
