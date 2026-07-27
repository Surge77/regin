<h1 align="center">regin</h1>

<p align="center">
  <em>Regin reforged Gram from its shards. This one reforges codebases from failing tests.</em>
</p>

---

A terminal coding agent — plan, edit, run tests, read the failure, iterate — and the benchmark
loop that proves whether changing it made it better.

> **Status: Phase 0 (scaffold).** No number to report yet. This README will state one or the
> project has failed on its own terms. See [`docs/PLAN.md`](docs/PLAN.md).

## Why another coding agent

There are two hundred of them. The difference here is the second half: every orchestration and
prompt change is A/B'd against a held-out benchmark, so claims look like

> changed the planner prompt, resolve-rate 31% → 38%, median cost $0.14/task

rather than "it feels smarter now". Scoring lives in the sibling repo
[`evalgate`](../evalgate); `regin` emits JSONL run records it consumes.

## Design commitments

- **No agent framework.** The loop is the deliverable, so nothing else owns control flow.
- **Anchored search/replace edits**, not model-generated unified diffs — far higher apply rate,
  and a failed match is a clean error the model can recover from.
- **Cost from provider `usage` fields**, including cache-read/write token classes. No estimates.
- **Every run carries a `config_hash`** so results are attributable to an exact configuration.
- **Sandboxed by default.** cwd jail + permission gate interactively; Docker per instance for benchmarks.

## Install

```bash
pip install -e ".[dev]"
cp .env.example .env   # add ANTHROPIC_API_KEY
regin --help
```

## Usage

```bash
regin fix "make the failing test in tests/test_parser.py pass"
regin fix "..." --yes          # auto-approve tools (benchmark mode)
regin trace last               # per-turn tokens, cost, latency
```

## Stack

Python 3.11+ · Typer · Pydantic v2 · Rich · Anthropic SDK behind a thin provider protocol ·
ripgrep + tree-sitter for code intelligence · SQLite + JSONL traces · Docker for benchmark sandboxes ·
pyright strict · pytest

## License

MIT
