# CoreCoder Agent

A faithful port of [CoreCoder](https://github.com/he-yufeng/CoreCoder.git) — a
~950 LoC distillation of Claude Code's coding loop — onto the OpenAgents
kernel.

It lives next to `production_coding_agent` to show that **product semantics
belong in plugin code, not in the kernel**. Both examples use the same SDK
seams; everything different about CoreCoder (tool roster, edit semantics,
context compression, sub-agent recursion) is plug-in code under `app/`.

## Why this example exists

`production_coding_agent` was written first as a deliberately conservative
demonstration: 5 tools, 12-message rolling window, simple text-only LLM calls.
CoreCoder is the same problem with the dial turned up — it shows what a
**real** coding agent looks like when the SDK's seams are pushed harder.

### Differences vs. `production_coding_agent`

| Concern | `production_coding_agent` | `corecoder_agent` |
|---|---|---|
| **Edit primitive** | `write_file` (whole-file overwrite) | `edit_file` with **strict-uniqueness search & replace** — `old_string` must occur exactly once; zero or multi matches raise `ModelRetryError` and the SDK feeds the failure back to the LLM for self-correction |
| **LLM interface** | `PatternPlugin.call_llm` (text-only) | Native Anthropic `tool_use`/`tool_result` content blocks driven directly through `ctx.llm_client.generate(messages, tools)` — the pattern owns the multi-turn loop |
| **Tool roster** | 5 read/write tools | 7 tools: `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `bash` (with denylist), `sub_agent` |
| **Shell access** | none | `bash` tool gated by 9 regex patterns blocking `rm -rf /`, fork bombs, `curl \| bash`, raw disk writes, etc., plus per-session `cwd` tracked in `ctx.scratch["bash_cwd"]` |
| **Context compression** | trim to last 12 messages | **3-layer progressive compressor** — Layer 1 snips long tool outputs (50% of budget), Layer 2 summarizes the older half via the LLM (70%), Layer 3 hard-collapses the middle (90%); records `layers_fired` in `ContextAssemblyResult.metadata` |
| **Persistent memory** | rolling delivery summaries | `CoreCoderMemory` carries dirty-file set, last cwd, last summary, and tool-usage `Counter` across sessions; injects them into the system prompt so the next run knows what was already touched |
| **Sub-agents** | none | `sub_agent` tool spawns a fresh `corecoder-subagent` (a sibling agent in the same `agent.json` with `sub_agent` removed for recursion safety) and returns a summary string |
| **System prompt** | mostly static | `CORE_PRINCIPLES` (read-before-write, exact-replace, verify, delegate) plus a per-run dynamic fragment with `cwd`, git branch/status, modified files, available tool names |

The kernel itself is unchanged. CoreCoder rides on:

- `pattern` seam → `CoreCoderPattern` for the native tool-calling loop
- `context_assembler` seam → `CompressingContextAssembler` for 3-layer compaction
- `memory` seam → `CoreCoderMemory` chained behind `window_buffer`
- `tool` seam → 7 plugins under `app/tools/`

## Structure

```text
corecoder_agent/
  agent.json              # two agents: "corecoder" + "corecoder-subagent"
  run_demo.py             # demo entrypoint
  .env.example            # LLM_API_BASE / LLM_API_KEY / LLM_MODEL
  app/
    pattern.py            # CoreCoderPattern (native tool-calling ReAct loop)
    context.py            # CompressingContextAssembler (3-layer compactor)
    memory.py             # CoreCoderMemory (persistent dirty-files + summaries)
    prompts.py            # CORE_PRINCIPLES + runtime fragment builder
    tools/
      read_file.py        # read with line-numbering and offset/limit
      write_file.py       # whole-file overwrite, tracks dirty_files
      edit_file.py        # strict-uniqueness search & replace
      glob_tool.py        # filesystem pattern match
      grep_tool.py        # ripgrep-style regex search
      bash_tool.py        # shell + 9-pattern regex denylist
      sub_agent.py        # spawns the sibling agent in a fresh session
  workspace/
    TASK.md               # task brief read by run_demo.py
    stats.py              # demo file with 2 intentional bugs
    test_stats.py         # unit tests that fail until the bugs are fixed
```

## Run

```bash
cp examples/corecoder_agent/.env.example examples/corecoder_agent/.env
# edit .env with your provider details

uv run python examples/corecoder_agent/run_demo.py
```

The demo briefs the agent with `workspace/TASK.md` and asks it to fix the two
bugs in `workspace/stats.py` until `workspace/test_stats.py` passes. Verify
manually:

```bash
cd examples/corecoder_agent/workspace
python -m unittest test_stats.py
```

## How the 3-layer compressor works

Every call to `assemble()` measures the transcript and runs layers in order:

1. **Layer 1 — snip** (≥ 50% of token budget). Walks every message; any
   `tool_result` block or string content longer than `tool_output_max_bytes`
   (default 2000) is replaced with its first 1000 + last 500 bytes plus a
   `[snipped N bytes]` marker. Lossless for what matters (start of stdout, tail
   of stack traces) and very cheap.

2. **Layer 2 — LLM summarize** (≥ 70%). Slices off the older half of the
   transcript (excluding the first `keep_first_messages` and last
   `keep_recent_messages_for_summary`) and asks the LLM for a tight one-paragraph
   summary preserving file paths edited, decisions made, unresolved errors. If
   no LLM client is wired (test mode), falls back to a deterministic head+tail
   line concatenation. Replaces the slice with a single `system` message.

3. **Layer 3 — hard collapse** (≥ 90%). Last-resort guard: keeps the first
   `keep_first_messages` (typically the original task) and the last
   `keep_last_messages_on_collapse` (the recent loop turns), drops everything
   between them with a placeholder.

`ContextAssemblyResult.metadata["layers_fired"]` lists which layers fired this
turn — observers and tests use it to verify compression behavior.

## Tests

```bash
uv run pytest -q tests/unit/examples/corecoder_agent/
```

Covers tool-side guarantees (edit_file uniqueness, bash denylist, per-session
cwd) and the three compression triggers.
