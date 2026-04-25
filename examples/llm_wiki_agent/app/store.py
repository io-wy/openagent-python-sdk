"""Persistent knowledge store: JSONL + inverted index."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from examples.llm_wiki_agent.app.protocols import SearchResult, WikiChunk, WikiSource


class WikiKnowledgeStore:
    """Append-only JSONL store with a simple in-memory inverted index.

    The index maps each normalised term to the set of chunk ids that
    contain it.  Search scores chunks by the number of query terms that
    appear in the chunk (AND semantics, tie-broken by term frequency).
    """

    def __init__(self, storage_dir: Path | str) -> None:
        self._dir = Path(storage_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sources_path = self._dir / "sources.jsonl"
        self._chunks_path = self._dir / "chunks.jsonl"
        self._index: dict[str, set[str]] = defaultdict(set)
        self._chunk_by_id: dict[str, WikiChunk] = {}
        self._source_by_url: dict[str, WikiSource] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _atomic_write(self, path: Path, lines: list[str]) -> None:
        fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for line in lines:
                    f.write(line + "\n")
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def reload(self) -> None:
        """Reload index from disk (call before read ops if another process may have written)."""
        self._index.clear()
        self._chunk_by_id.clear()
        self._source_by_url.clear()
        self._load()

    def _load(self) -> None:
        if self._sources_path.exists():
            for line in self._sources_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                src = WikiSource.from_dict(json.loads(line))
                self._source_by_url[src.url] = src
        if self._chunks_path.exists():
            for line in self._chunks_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                chunk = WikiChunk.from_dict(json.loads(line))
                self._chunk_by_id[chunk.chunk_id] = chunk
                self._index_chunk(chunk)

    def _index_chunk(self, chunk: WikiChunk) -> None:
        topics_flat = _flatten(chunk.topics)
        text = f"{chunk.content} {chunk.summary} {' '.join(chunk.entities)} {' '.join(topics_flat)}"
        for term in _tokenise(text):
            self._index[term].add(chunk.chunk_id)

    def _persist_source(self, source: WikiSource) -> None:
        lines = [json.dumps(s.to_dict(), ensure_ascii=False) for s in self._source_by_url.values()]
        self._atomic_write(self._sources_path, lines)

    def _persist_chunks(self, chunks: list[WikiChunk]) -> None:
        existing = list(self._chunk_by_id.values())
        lines = [json.dumps(c.to_dict(), ensure_ascii=False) for c in existing]
        self._atomic_write(self._chunks_path, lines)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_source(self, source: WikiSource, chunks: list[WikiChunk]) -> None:
        """Store a source and its chunks atomically."""
        self._source_by_url[source.url] = source
        for chunk in chunks:
            self._chunk_by_id[chunk.chunk_id] = chunk
            self._index_chunk(chunk)
        self._persist_source(source)
        self._persist_chunks(chunks)

    def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """Keyword search over chunk content / summary / entities / topics."""
        terms = _tokenise(query)
        if not terms:
            return []

        scores: dict[str, float] = defaultdict(float)
        for term in terms:
            for chunk_id in self._index.get(term, set()):
                scores[chunk_id] += 1.0

        # Tie-break by term frequency within the chunk
        for chunk_id, match_count in scores.items():
            chunk = self._chunk_by_id[chunk_id]
            text = f"{chunk.content} {chunk.summary}"
            total = 0
            for t in terms:
                total += len(re.findall(r"\b" + re.escape(t) + r"\b", text, re.IGNORECASE))
            scores[chunk_id] = match_count + total * 0.01

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [
            SearchResult(chunk=self._chunk_by_id[cid], score=score)
            for cid, score in ranked[:top_k]
        ]

    def list_sources(self) -> list[WikiSource]:
        return list(self._source_by_url.values())

    def get_source_chunks(self, url: str) -> list[WikiChunk]:
        return [c for c in self._chunk_by_id.values() if c.source_url == url]

    def source_count(self) -> int:
        return len(self._source_by_url)

    def chunk_count(self) -> int:
        return len(self._chunk_by_id)

    def topic_list(self) -> list[str]:
        topics: set[str] = set()
        for chunk in self._chunk_by_id.values():
            topics.update(_flatten(chunk.topics))
        return sorted(topics)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_RE_WORD = re.compile(r"[a-zA-Z0-9一-鿿]+")


def _flatten(items: list[Any]) -> list[str]:
    """Flatten a list that may contain nested lists."""
    out: list[str] = []
    for item in items:
        if isinstance(item, list):
            out.extend(str(x) for x in item)
        else:
            out.append(str(item))
    return out


def _tokenise(text: str) -> list[str]:
    """Lower-case, de-duplicate token list."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _RE_WORD.finditer(text.lower()):
        tok = m.group(0)
        if tok not in seen and len(tok) > 1:
            seen.add(tok)
            out.append(tok)
    return out


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
