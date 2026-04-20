# Production Coding Agent

This example is a production-style coding agent built on the OpenAgents kernel.

It demonstrates:

- explicit task-packet assembly
- persistent coding memory
- safe tool execution with filesystem boundaries
- local follow-up semantics
- structured delivery artifacts
- benchmark-style evaluation harness
- **durable execution**: `run_demo.py` passes `durable=True` — if an upstream LLM
  rate-limit / connection error hits mid-run, the runtime auto-loads the most
  recent step checkpoint and resumes (bounded by `RunBudget.max_resume_attempts`).

It is intentionally a strong example, not a claim that local tests alone can
certify market readiness.

## Structure

```text
production_coding_agent/
  agent.json
  run_demo.py
  run_benchmark.py
  app/
    protocols.py
    plugins.py
    benchmark.py
  benchmarks/
    tasks.json
  workspace/
    PRODUCT_BRIEF.md
    app/
    tests/
  outputs/
```

### What Lives Where

- `agent.json`
  - runtime wiring
- `run_demo.py`
  - interactive demo entrypoint
- `run_benchmark.py`
  - local benchmark harness entrypoint
- `app/protocols.py`
  - structured protocol objects
- `app/plugins.py`
  - memory, context assembler, follow-up, repair, and pattern
- `app/benchmark.py`
  - deterministic benchmark runner
- `benchmarks/tasks.json`
  - benchmark task set
- `workspace/`
  - simulated repository to inspect
- `outputs/`
  - generated delivery artifacts

Run:

```bash
# Canonical entry — goes through the built-in CLI.
openagents run examples/production_coding_agent/agent.json \
    --input "implement TicketService.close_ticket and add tests"

# Interactive multi-turn chat against the same agent:
openagents chat examples/production_coding_agent/agent.json

# Legacy demo script — equivalent, kept for illustration:
uv run python examples/production_coding_agent/run_demo.py
```

Benchmark (unchanged — this is a harness, not a one-shot run):

```bash
uv run python examples/production_coding_agent/run_benchmark.py
```
