"""Wiki agent SDK plugins: tools, memory, context assembler, pattern."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openagents.interfaces.capabilities import PATTERN_EXECUTE, PATTERN_REACT, TOOL_INVOKE
from openagents.interfaces.context import ContextAssemblerPlugin, ContextAssemblyResult
from openagents.interfaces.memory import MemoryPlugin
from openagents.interfaces.pattern import PatternPlugin
from openagents.interfaces.tool import ToolPlugin
from openagents.plugins.builtin.pattern.react import ReActPattern

from examples.llm_wiki_agent.app.protocols import WikiChunk, WikiSource
from examples.llm_wiki_agent.app.store import WikiKnowledgeStore, content_hash


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------


class DeepReadUrlTool(ToolPlugin):
    """Fetch a URL via opencli web read and return the full markdown content.

    This is designed for deep reading scenarios where the agent needs the
    complete article text to produce an exhaustive analysis.
    """

    name = "deep_read_url"
    description = (
        "Fetch a web page using opencli and return the FULL markdown content. "
        "Use this when the user wants a detailed analysis of an article."
    )
    durable_idempotent = True

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})
        self._timeout = self.config.get("timeout_seconds", 60)
        self._max_length = self.config.get("max_length", 200_000)

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        url = params.get("url", "")
        if not url:
            return {"success": False, "error": "url is required"}

        import shutil
        import tempfile

        opencli_path = shutil.which("opencli")
        if not opencli_path:
            return {"success": False, "error": "opencli not found in PATH. Install with: npm install -g @jackwener/opencli"}

        out_dir = Path(tempfile.mkdtemp(prefix="deepread_"))
        try:
            proc = await asyncio.create_subprocess_exec(
                opencli_path, "web", "read",
                "--url", url,
                "--output", str(out_dir),
                "--format", "md",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                return {"success": False, "error": f"opencli failed (rc={proc.returncode}): {stderr[:500]}"}

            md_files = list(out_dir.rglob("*.md"))
            if not md_files:
                return {"success": False, "error": "opencli produced no markdown file"}

            content = md_files[0].read_text(encoding="utf-8")[: self._max_length]
            title = _extract_title_from_markdown(content) or url

            return {
                "success": True,
                "url": url,
                "title": title,
                "content": content,
                "word_count": len(content.split()),
            }
        except asyncio.TimeoutError:
            return {"success": False, "error": f"opencli timed out after {self._timeout}s"}
        except FileNotFoundError:
            return {"success": False, "error": "opencli not found. Install with: npm install -g @jackwener/opencli"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch and analyze"},
            },
            "required": ["url"],
        }


class AddSourceTool(ToolPlugin):
    """Persist a source and its chunks into the knowledge base."""

    name = "add_source"
    description = (
        "Store a processed web page into the knowledge base. "
        "Provide url, title, chunks (each with content, summary, entities, topics)."
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})
        storage_dir = self.config.get("storage_dir", "examples/llm_wiki_agent/knowledge")
        self._store = WikiKnowledgeStore(Path(storage_dir))

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        url = params.get("url", "")
        if not url:
            return {"success": False, "error": "url is required"}

        title = params.get("title", "")
        raw_chunks = params.get("chunks", [])
        fetched_at = params.get("fetched_at", datetime.now(timezone.utc).isoformat())

        source = WikiSource(
            url=url,
            title=title,
            fetched_at=fetched_at,
            content_hash=content_hash(json.dumps(raw_chunks, ensure_ascii=False)),
        )
        chunks: list[WikiChunk] = []
        for idx, rc in enumerate(raw_chunks):
            chunks.append(
                WikiChunk(
                    chunk_id=f"{content_hash(url)}-{idx}",
                    source_url=url,
                    content=rc.get("content", ""),
                    summary=rc.get("summary", ""),
                    entities=rc.get("entities", []),
                    topics=rc.get("topics", []),
                    created_at=fetched_at,
                )
            )

        self._store.add_source(source, chunks)
        return {
            "success": True,
            "source_url": url,
            "chunks_stored": len(chunks),
            "total_sources": self._store.source_count(),
        }

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Source URL"},
                "title": {"type": "string", "description": "Page title"},
                "fetched_at": {"type": "string", "description": "ISO timestamp"},
                "chunks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "summary": {"type": "string"},
                            "entities": {"type": "array", "items": {"type": "string"}},
                            "topics": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["content"],
                    },
                },
            },
            "required": ["url", "chunks"],
        }


class SearchKbTool(ToolPlugin):
    """Search the knowledge base by keywords."""

    name = "search_kb"
    description = "Search the knowledge base for relevant chunks matching a query."

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})
        storage_dir = self.config.get("storage_dir", "examples/llm_wiki_agent/knowledge")
        self._store = WikiKnowledgeStore(Path(storage_dir))

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        self._store.reload()
        query = params.get("query", "")
        top_k = params.get("top_k", 5)
        if not query:
            return {"success": False, "error": "query is required"}

        results = self._store.search(query, top_k=top_k)
        return {
            "success": True,
            "query": query,
            "results": [
                {
                    "chunk_id": r.chunk.chunk_id,
                    "source_url": r.chunk.source_url,
                    "content_preview": r.chunk.content[:500],
                    "summary": r.chunk.summary,
                    "score": round(r.score, 2),
                }
                for r in results
            ],
        }

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "top_k": {"type": "integer", "default": 5, "description": "Max results"},
            },
            "required": ["query"],
        }


class IngestUrlTool(ToolPlugin):
    """Fetch a URL via opencli web read, chunk, and store in the knowledge base."""

    name = "ingest_url"
    description = (
        "Fetch a web page using opencli, split it into chunks, and store them in the knowledge base. "
        "Provide the URL. This is a one-step operation that does fetch + chunk + add_source."
    )
    durable_idempotent = False

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})
        self._timeout = self.config.get("timeout_seconds", 60)
        self._max_length = self.config.get("max_length", 200_000)
        self._chunk_size = self.config.get("chunk_size", 2000)
        storage_dir = self.config.get("storage_dir", "examples/llm_wiki_agent/knowledge")
        self._store = WikiKnowledgeStore(Path(storage_dir))

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        url = params.get("url", "")
        if not url:
            return {"success": False, "error": "url is required"}

        import shutil
        import tempfile

        opencli_path = shutil.which("opencli")
        if not opencli_path:
            return {"success": False, "error": "opencli not found in PATH. Install with: npm install -g @jackwener/opencli"}

        out_dir = Path(tempfile.mkdtemp(prefix="wiki_"))
        try:
            proc = await asyncio.create_subprocess_exec(
                opencli_path, "web", "read",
                "--url", url,
                "--output", str(out_dir),
                "--format", "md",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                return {"success": False, "error": f"opencli failed (rc={proc.returncode}): {stderr[:500]}"}

            # Find the generated markdown file
            md_files = list(out_dir.rglob("*.md"))
            if not md_files:
                return {"success": False, "error": "opencli produced no markdown file"}

            content = md_files[0].read_text(encoding="utf-8")[: self._max_length]
            title = _extract_title_from_markdown(content) or url
            fetched_at = datetime.now(timezone.utc).isoformat()

            # Chunk by paragraphs (~chunk_size chars)
            paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
            chunks: list[WikiChunk] = []
            current = ""
            idx = 0
            for para in paragraphs:
                if len(current) + len(para) > self._chunk_size and current:
                    chunks.append(
                        WikiChunk(
                            chunk_id=f"{content_hash(url)}-{idx}",
                            source_url=url,
                            content=current.strip(),
                            summary="",
                            entities=[],
                            topics=[],
                            created_at=fetched_at,
                        )
                    )
                    idx += 1
                    current = para
                else:
                    current += "\n\n" + para if current else para
            if current.strip():
                chunks.append(
                    WikiChunk(
                        chunk_id=f"{content_hash(url)}-{idx}",
                        source_url=url,
                        content=current.strip(),
                        summary="",
                        entities=[],
                        topics=[],
                        created_at=fetched_at,
                    )
                )

            source = WikiSource(
                url=url,
                title=title,
                fetched_at=fetched_at,
                content_hash=content_hash(content),
            )
            self._store.add_source(source, chunks)
            return {
                "success": True,
                "url": url,
                "title": title,
                "chunks_stored": len(chunks),
                "total_sources": self._store.source_count(),
                "total_chunks": self._store.chunk_count(),
            }
        except asyncio.TimeoutError:
            return {"success": False, "error": f"opencli timed out after {self._timeout}s"}
        except FileNotFoundError:
            return {"success": False, "error": "opencli not found. Install with: npm install -g @jackwener/opencli"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to ingest"},
            },
            "required": ["url"],
        }


class ListSourcesTool(ToolPlugin):
    """List all ingested sources."""

    name = "list_sources"
    description = "List all sources currently stored in the knowledge base."

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})
        storage_dir = self.config.get("storage_dir", "examples/llm_wiki_agent/knowledge")
        self._store = WikiKnowledgeStore(Path(storage_dir))

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        self._store.reload()
        sources = self._store.list_sources()
        return {
            "success": True,
            "count": len(sources),
            "sources": [
                {
                    "url": s.url,
                    "title": s.title,
                    "fetched_at": s.fetched_at,
                    "content_hash": s.content_hash,
                }
                for s in sources
            ],
        }

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }


# ------------------------------------------------------------------
# Memory
# ------------------------------------------------------------------


class WikiMemory(MemoryPlugin):
    """Inject KB stats into memory_view; writeback persists session summary."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config=config)
        storage_dir = self.config.get("storage_dir", "examples/llm_wiki_agent/knowledge")
        self._store = WikiKnowledgeStore(Path(storage_dir))
        self._session_dir = Path(self.config.get("session_dir", "examples/llm_wiki_agent/sessions"))
        self._session_dir.mkdir(parents=True, exist_ok=True)

    async def inject(self, context: Any) -> None:
        self._store.reload()
        stats = {
            "source_count": self._store.source_count(),
            "chunk_count": self._store.chunk_count(),
            "topics": self._store.topic_list(),
        }
        context.memory_view["wiki_kb_stats"] = stats

    async def writeback(self, context: Any) -> None:
        summary = {
            "session_id": context.session_id,
            "run_id": context.run_id,
            "input_text": context.input_text,
            "tool_results": [
                {"tool_id": tr["tool_id"], "result_preview": str(tr["result"])[:200]}
                for tr in getattr(context, "tool_results", [])
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        path = self._session_dir / f"{context.session_id}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------
# Context Assembler
# ------------------------------------------------------------------


class WikiContextAssembler(ContextAssemblerPlugin):
    """Inject KB stats and usage hints into the assembled context."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        storage_dir = self.config.get("knowledge_dir", "examples/llm_wiki_agent/knowledge")
        self._store = WikiKnowledgeStore(Path(storage_dir))

    async def assemble(
        self,
        *,
        request: Any,
        session_state: dict[str, Any],
        session_manager: Any,
    ) -> ContextAssemblyResult:
        self._store.reload()
        hints: list[str] = []
        count = self._store.source_count()
        if count == 0:
            hints.append(
                "The knowledge base is empty. "
                "If the user provides a URL, call ingest_url to fetch and store it."
            )
        else:
            hints.append(
                f"Knowledge base has {count} source(s) and {self._store.chunk_count()} chunk(s). "
                f"Topics: {', '.join(self._store.topic_list()[:10]) or 'N/A'}."
            )
        return ContextAssemblyResult(
            transcript=[{"role": "system", "content": "\n".join(hints)}],
            metadata={"wiki_kb_ready": count > 0},
        )


# ------------------------------------------------------------------
# Pattern
# ------------------------------------------------------------------


class WikiPattern(ReActPattern):
    """ReAct pattern with wiki-agent-specific system prompt and follow-up shortcuts.

    Overrides ``execute()`` to feed tool results back into the LLM so the
    agent can perform multi-step reasoning (fetch → chunk → add_source).
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config=config or {})

    def _llm_system_prompt(self) -> str:
        return self.compose_system_prompt(
            "You are a Wiki Agent. Your job is to build and query a knowledge base from web pages, "
            "and to perform deep, thorough analysis of articles when requested.\n\n"
            "## Workflow\n"
            "1. **Ingest**: call ingest_url with the URL → it fetches, chunks, and stores automatically\n"
            "2. **Query**: call search_kb with the question → synthesize answer from returned chunks\n"
            "3. **List**: call list_sources when asked what you know\n"
            "4. **Deep Read / Analyze**: call deep_read_url with the URL → you receive the FULL markdown content → "
            "output a COMPLETE, EXHAUSTIVE, and DETAILED Markdown analysis.\n\n"
            "## Analysis Rules (CRITICAL)\n"
            "When the user asks you to analyze, read deeply, or review an article:\n"
            "- Call deep_read_url EXACTLY ONCE to get the full content\n"
            "- After receiving the tool result, IMMEDIATELY output final with your analysis\n"
            "- Do NOT call deep_read_url again — you already have the content\n"
            "- Your final output must be THOROUGH, COMPLETE, and EXHAUSTIVE\n"
            "- Do NOT summarize, abbreviate, or be brief\n"
            "- Cover every major point, argument, and detail from the article\n"
            "- Use proper Markdown formatting (headings, bullet points, quotes)\n"
            "- The user wants the FULL picture, not a summary\n\n"
            "## Response format (STRICT JSON, no markdown, no extra text)\n"
            'For tool calls: {"type":"tool_call","tool":"<tool_id>","params":{"key":"value"}}\n'
            'For final answer: {"type":"final","content":"your answer here"}\n'
            "IMPORTANT: Use straight double quotes ONLY. No smart quotes, no extra quotes."
        )

    def _parse_llm_action(self, raw: str) -> dict[str, Any]:
        """Parse LLM output with extra fault tolerance for malformed JSON."""
        # Try strict JSON first
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        # Try first JSON block
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            snippet = raw[start : end + 1]
            # Fix common LLM JSON errors
            snippet = re.sub(r'""+', '"', snippet)  # double quotes
            snippet = re.sub(r'\n+\s*"', '"', snippet)  # newline before quote
            try:
                data = json.loads(snippet)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass

            # Regex fallback for tool_call
            m = re.search(r'"type"\s*:\s*"tool_call"', snippet)
            if m:
                tool_m = re.search(r'"tool"\s*:\s*"([^"]+)"', snippet)
                params_m = re.search(r'"params"\s*:\s*(\{[^}]*\})', snippet)
                if tool_m:
                    try:
                        params = json.loads(params_m.group(1)) if params_m else {}
                    except json.JSONDecodeError:
                        params = {}
                    return {"type": "tool_call", "tool": tool_m.group(1), "params": params}

        return {"type": "final", "content": raw}

    async def resolve_followup(self, *, context: Any) -> Any:
        """Short-circuit simple KB queries without calling the LLM."""
        text = (context.input_text or "").lower().strip()
        kb = context.memory_view.get("wiki_kb_stats", {})

        if any(p in text for p in ("what sources", "list sources", "what do you know", "kb status")):
            count = kb.get("source_count", 0)
            topics = kb.get("topics", [])
            msg = f"I have {count} source(s) in the knowledge base."
            if topics:
                msg += f" Topics: {', '.join(topics[:10])}."
            if count == 0:
                msg += " Give me a URL to ingest."

            from openagents.interfaces.followup import FollowupResolution

            return FollowupResolution(status="resolved", output=msg)
        return None

    async def execute(self) -> Any:
        """Multi-step ReAct: tool results are fed back to the LLM."""
        self._inject_validation_correction()
        ctx = self.context

        resolution = await self.resolve_followup(context=ctx)
        if resolution is not None and resolution.status == "resolved":
            if ctx.state is not None:
                ctx.state["_runtime_last_output"] = resolution.output
                ctx.state["resolved_by"] = "resolve_followup"
            return resolution.output

        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._llm_system_prompt()},
            {"role": "user", "content": self._llm_user_prompt()},
        ]

        max_steps = self._max_steps()
        timeout_s = self._step_timeout_ms() / 1000

        for step in range(max_steps):
            await self.emit("pattern.step_started", step=step)

            try:
                raw = await asyncio.wait_for(
                    self.call_llm(
                        messages=messages,
                        model=getattr(ctx.llm_options, "model", None) if ctx.llm_options else None,
                        temperature=getattr(ctx.llm_options, "temperature", None) if ctx.llm_options else None,
                        max_tokens=getattr(ctx.llm_options, "max_tokens", None) if ctx.llm_options else None,
                    ),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f"Pattern step timed out after {self._step_timeout_ms()}ms at step {step}") from exc

            action = self._parse_llm_action(raw)
            await self.emit("pattern.step_finished", step=step, action=action)

            if not isinstance(action, dict):
                raise TypeError(f"Pattern action must be dict, got {type(action).__name__}")

            action_type = action.get("type")
            if not isinstance(action_type, str) or not action_type.strip():
                raise ValueError("Pattern action must include a non-empty string 'type'")

            if action_type == "tool_call":
                tool_id = action.get("tool") or action.get("tool_id")
                if not isinstance(tool_id, str) or not tool_id:
                    raise ValueError("tool_call action must include non-empty 'tool' or 'tool_id'")
                params = action.get("params", {}) or {}
                if not isinstance(params, dict):
                    raise ValueError("tool_call action 'params' must be an object")

                result = await self.call_tool(tool_id, params)
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": f"Tool result for {tool_id}:\n{json.dumps(result, ensure_ascii=False, indent=2)[:4000]}",
                    }
                )
                continue

            if action_type == "final":
                content = action.get("content")
                ctx.state["_runtime_last_output"] = content
                return content

            # action_type == "continue"
            messages.append({"role": "assistant", "content": raw})
            continue

        raise RuntimeError(f"Pattern exceeded max_steps ({max_steps})")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _extract_title_from_markdown(md: str) -> str:
    m = re.search(r"^#\s+(.+)$", md, re.MULTILINE)
    return (m.group(1).strip() if m else "")[:200]
