"""Tests for WikiKnowledgeStore."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from examples.llm_wiki_agent.app.protocols import WikiChunk, WikiSource
from examples.llm_wiki_agent.app.store import WikiKnowledgeStore, content_hash


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield WikiKnowledgeStore(tmp)


class TestWikiKnowledgeStore:
    def test_add_source_and_search(self, store: WikiKnowledgeStore) -> None:
        source = WikiSource(url="https://example.com/ai", title="AI Overview")
        chunks = [
            WikiChunk(
                chunk_id="c1",
                source_url="https://example.com/ai",
                content="Transformers are a type of neural network architecture.",
                summary="Intro to transformers",
                entities=["transformer", "neural network"],
                topics=["AI", "deep learning"],
            ),
            WikiChunk(
                chunk_id="c2",
                source_url="https://example.com/ai",
                content="Attention mechanisms allow models to focus on relevant parts.",
                summary="Attention mechanism explained",
                entities=["attention", "model"],
                topics=["AI", "NLP"],
            ),
        ]
        store.add_source(source, chunks)

        assert store.source_count() == 1
        assert store.chunk_count() == 2

        results = store.search("transformer architecture")
        assert len(results) >= 1
        assert any(r.chunk.chunk_id == "c1" for r in results)

    def test_search_ranking(self, store: WikiKnowledgeStore) -> None:
        source = WikiSource(url="https://example.com/ml", title="ML")
        chunks = [
            WikiChunk(
                chunk_id="a",
                source_url="https://example.com/ml",
                content="Python is a programming language.",
                summary="Python intro",
                entities=["python"],
                topics=["programming"],
            ),
            WikiChunk(
                chunk_id="b",
                source_url="https://example.com/ml",
                content="Python machine learning libraries include PyTorch and TensorFlow.",
                summary="Python ML libs",
                entities=["python", "pytorch", "tensorflow"],
                topics=["ML", "python"],
            ),
        ]
        store.add_source(source, chunks)

        results = store.search("python machine learning", top_k=2)
        assert len(results) == 2
        # Chunk 'b' has more matches (python + machine + learning)
        assert results[0].chunk.chunk_id == "b"

    def test_list_sources(self, store: WikiKnowledgeStore) -> None:
        s1 = WikiSource(url="https://a.com", title="A")
        s2 = WikiSource(url="https://b.com", title="B")
        store.add_source(s1, [])
        store.add_source(s2, [])

        sources = store.list_sources()
        assert len(sources) == 2
        urls = {s.url for s in sources}
        assert urls == {"https://a.com", "https://b.com"}

    def test_persistence(self, store: WikiKnowledgeStore) -> None:
        source = WikiSource(url="https://persist.com", title="Persist")
        chunks = [
            WikiChunk(
                chunk_id="p1",
                source_url="https://persist.com",
                content="Persistent storage test.",
                summary="Test",
                entities=["storage"],
                topics=["testing"],
            )
        ]
        store.add_source(source, chunks)

        # Re-open the same directory
        store2 = WikiKnowledgeStore(store._dir)
        assert store2.source_count() == 1
        assert store2.chunk_count() == 1
        results = store2.search("persistent storage")
        assert len(results) == 1
        assert results[0].chunk.chunk_id == "p1"

    def test_topic_list(self, store: WikiKnowledgeStore) -> None:
        source = WikiSource(url="https://topics.com", title="Topics")
        chunks = [
            WikiChunk(
                chunk_id="t1",
                source_url="https://topics.com",
                content="x",
                summary="x",
                entities=[],
                topics=["AI", "NLP"],
            ),
            WikiChunk(
                chunk_id="t2",
                source_url="https://topics.com",
                content="y",
                summary="y",
                entities=[],
                topics=[["AI", "vision"]],
            ),
        ]
        store.add_source(source, chunks)

        topics = store.topic_list()
        assert "AI" in topics

    def test_empty_search(self, store: WikiKnowledgeStore) -> None:
        assert store.search("anything") == []

    def test_content_hash(self) -> None:
        h1 = content_hash("hello")
        h2 = content_hash("hello")
        h3 = content_hash("world")
        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 16
