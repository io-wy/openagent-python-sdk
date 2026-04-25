# LLM Wiki Agent

An example agent that ingests web pages via **opencli** and answers questions from a local knowledge base.

## What it demonstrates

- **Custom tools** (`ingest_url`, `deep_read_url`, `search_kb`, `list_sources`) backed by opencli + JSONL store
- **Custom pattern** (`WikiPattern`) extending `ReActPattern` for multi-step reasoning with tool results fed back to the LLM
- **Custom memory** (`WikiMemory`) injecting KB stats into the context
- **Custom context assembler** (`WikiContextAssembler`) providing KB hints to the LLM
- **OpenCLI integration** — `IngestUrlTool` and `DeepReadUrlTool` call `opencli web read` internally via subprocess to fetch and convert web pages to Markdown

## Directory structure

```
examples/llm_wiki_agent/
├── agent.json              # Runtime configuration
├── run_demo.py             # Entry point
├── README.md
├── .env.example            # LLM credentials template
├── app/
│   ├── protocols.py        # WikiSource, WikiChunk, SearchResult
│   ├── store.py            # JSONL persistence + inverted index
│   └── plugins.py          # Tools, Memory, ContextAssembler, Pattern
├── knowledge/              # Persistent JSONL storage (gitignored)
└── sessions/               # Session summaries (gitignored)
```

## Prerequisites

1. **opencli** installed globally:
   ```bash
   npm install -g @jackwener/opencli
   ```
2. **LLM credentials** copied from `.env.example`:
   ```bash
   cp examples/llm_wiki_agent/.env.example examples/llm_wiki_agent/.env
   # Edit .env with your LLM_API_KEY / LLM_API_BASE / LLM_MODEL
   ```

## Run

```bash
uv run python examples/llm_wiki_agent/run_demo.py
```

Four scenarios are demonstrated:
1. **Ingest** — fetch a Wikipedia page via `opencli web read`, chunk + summarize, store in KB
2. **Query** — search KB for relevant chunks and synthesize an answer
3. **List** — enumerate all ingested sources
4. **Deep Read / Analyze** — fetch a URL and generate a thorough, exhaustive Markdown analysis (not a summary)

## Architecture

```
User Input
    |
    v
[WikiPattern] -- ReAct loop (multi-step, tool results fed back to LLM)
    |
    +-- "ingest <url>" --> [ingest_url tool]
    |                           |
    |                           +-- opencli web read --url <url> --format md
    |                           +-- chunk + summarize via LLM
    |                           +-- store to JSONL (WikiKnowledgeStore)
    |
    +-- "analyze <url>" --> [deep_read_url tool]
    |                           |
    |                           +-- opencli web read --url <url> --format md
    |                           +-- return full markdown content
    |                           |
    |                           v
    |                    [LLM: exhaustive Markdown analysis]
    |
    +-- "query <question>" --> [search_kb tool] --> ranked chunks
    |                           |
    |                           v
    |                    [LLM: synthesize answer]
    |
    +-- "list sources" --> [list_sources tool] --> source metadata
```

## Knowledge store

- **Zero new Python dependencies** — pure stdlib JSONL + inverted index
- **Atomic writes** — write to temp file, then `os.replace`
- **Keyword search** — AND semantics with frequency tie-breaking
- **Cross-instance sync** — each read op calls `reload()` to pick up writes from other tool instances

## Extending

- Swap `WikiKnowledgeStore` for ChromaDB / mem0 for semantic search
- Add `delete_source` tool for KB curation
- Add `batch_ingest` tool for sitemap crawling
