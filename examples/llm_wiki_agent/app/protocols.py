"""Data models for the wiki knowledge base."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WikiSource:
    """An ingested web page."""

    url: str
    title: str = ""
    fetched_at: str = ""
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "fetched_at": self.fetched_at,
            "content_hash": self.content_hash,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WikiSource:
        return cls(
            url=data["url"],
            title=data.get("title", ""),
            fetched_at=data.get("fetched_at", ""),
            content_hash=data.get("content_hash", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class WikiChunk:
    """A searchable fragment of a source page."""

    chunk_id: str
    source_url: str
    content: str
    summary: str = ""
    entities: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "source_url": self.source_url,
            "content": self.content,
            "summary": self.summary,
            "entities": self.entities,
            "topics": self.topics,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WikiChunk:
        return cls(
            chunk_id=data["chunk_id"],
            source_url=data["source_url"],
            content=data["content"],
            summary=data.get("summary", ""),
            entities=data.get("entities", []),
            topics=data.get("topics", []),
            created_at=data.get("created_at", ""),
        )


@dataclass(frozen=True)
class SearchResult:
    """A ranked chunk returned from a knowledge base query."""

    chunk: WikiChunk
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"chunk": self.chunk.to_dict(), "score": self.score}
