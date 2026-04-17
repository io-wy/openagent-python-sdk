# Contributing to OpenAgents SDK

Thank you for your interest in contributing. This document covers how to get set up, the project's conventions, and what to expect during review.

## Prerequisites

- Python 3.10+
- [`uv`](https://github.com/astral-sh/uv) for environment and dependency management

## Development Setup

```bash
git clone https://github.com/<your-fork>/openagent-python-sdk.git
cd openagent-python-sdk
uv sync
```

Run the full test suite:

```bash
uv run pytest -q
```

Check coverage (floor is 90%):

```bash
uv run coverage run -m pytest && uv run coverage report
```

## Making Changes

### Source + Tests Together

Any change under `openagents/` **must** include the corresponding test change in the same PR. This is a hard rule — see `AGENTS.md`. Do not land source changes without tests.

### Architecture Constraints

This SDK is a single-agent runtime kernel. Before adding something, read `CLAUDE.md` (Architecture section) and `docs/seams-and-extension-points.md` to understand which layer your change belongs to. Do not push product semantics into the kernel.

### Commit Style

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(context): add sliding-window assembler
fix(loader): handle missing impl key gracefully
docs(readme): update quickstart example
chore(deps): bump pydantic to 2.7
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`

## Submitting a Pull Request

1. Fork the repo and create a branch from `main`.
2. Make your changes with tests.
3. Verify the full suite passes: `uv run pytest -q`
4. Open a PR against `main` and fill in the PR template.

## Reporting Issues

Use the GitHub issue templates:
- **Bug Report** — for reproducible defects
- **Feature Request** — for new capabilities or enhancements

## License

By contributing, you agree that your contributions will be licensed under the [Apache 2.0 License](LICENSE).
