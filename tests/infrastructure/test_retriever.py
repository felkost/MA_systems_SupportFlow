"""Knowledge-base loading (pure — no ML) and hybrid retrieval (Chroma +
BM25 via `EnsembleRetriever`, docs/decisions.md #7). The real embedding
model is never loaded in `pytest` — a deterministic fake `Embeddings`
stands in, so this exercises the real `EnsembleRetriever` combination
logic without the multi-second `sentence-transformers` download/load cost
in CI.
"""

from langchain_core.embeddings import Embeddings

from src.infrastructure.retriever import build_retriever, load_knowledge_base


class _FakeEmbeddings(Embeddings):
    """Deterministic, dependency-free stand-in for a real embedding
    model — hashes each text into a small fixed-size vector so identical
    texts get identical vectors and Chroma's similarity search is
    well-defined without downloading anything.
    """

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return [float((hash(text) >> (8 * i)) % 256) for i in range(8)]


def test_load_knowledge_base_returns_faq_service_and_dialogue_chunks() -> None:
    chunks = load_knowledge_base()

    ids = {c.id for c in chunks}
    assert "faq-01" in ids
    assert "svc-01" in ids
    assert "dlg-01" in ids
    assert all(c.text and c.source and c.retrieved_at for c in chunks)


def test_load_knowledge_base_covers_task_size_ranges() -> None:
    # task §5: 15-25 FAQ, 2-5 service pages, 5-10 example dialogues.
    chunks = load_knowledge_base()
    faq = [c for c in chunks if c.id.startswith("faq-")]
    svc = [c for c in chunks if c.id.startswith("svc-")]
    dlg = [c for c in chunks if c.id.startswith("dlg-")]

    assert 15 <= len(faq) <= 25
    assert 2 <= len(svc) <= 5
    assert 5 <= len(dlg) <= 10


def test_build_retriever_finds_the_matching_faq_chunk() -> None:
    chunks = load_knowledge_base()
    retriever = build_retriever(chunks, embedding_function=_FakeEmbeddings())

    results = retriever.invoke("бонусна картка Власний Рахунок")

    assert any(
        "faq-02" == r.metadata["id"] or "svc-01" == r.metadata["id"] for r in results
    )
