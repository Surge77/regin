# Contributing to regin

Thanks for looking. This project has an unusual bar for one class of change, so please read the
first section before opening a PR that touches the agent's behaviour.

## The one rule that makes this project different

**A change to orchestration or prompts does not merge without a re-scored before/after.**

The agent is the cheap half; the measurement is the point. A change that cannot be attributed to
a number does not go in. Concretely, if you touch any of:

- the system prompt or any prompt template,
- the agent loop's control flow,
- the tool surface or a tool's description,
- retry, compaction, or context policy,

then the PR description needs a table like this:

```
                     before        after
resolve-rate         31.2%    →    35.8%     (n=300, swe-bench-lite)
median $/task        $0.14    →    $0.16
p95 latency          181s     →    174s
mean turns           11.3     →    9.8
```

Produce it with:

```bash
regin bench run   --tasks tasks/swe-bench-lite.jsonl --agent regin --max-cost 25
regin bench score --records "runs/bench/*-records.jsonl" --verdicts verdicts.json
```

"It feels better" is not evidence, and neither is a completion rate — see
[`docs/BENCHMARK.md`](docs/BENCHMARK.md) for why those differ.

If you cannot run a sweep (it costs real money), say so in the PR. A maintainer can run it. Do
not merge on vibes and backfill the number later; that is how a benchmark stops meaning anything.

Changes that do **not** need a sweep: bug fixes with a failing test, typing fixes, docs,
refactors that provably preserve behaviour, and new tests.

## Getting set up

```bash
git clone https://github.com/Surge77/regin.git
cd regin
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

You will also want [ripgrep](https://github.com/BurntSushi/ripgrep) on `PATH` — the `grep` tool
shells out to it, and its tests skip without it. For benchmark work, see
[`docs/BENCHMARK.md`](docs/BENCHMARK.md).

## The gates

All three must be green. CI runs exactly these:

```bash
ruff check src tests scorers
pyright                                              # strict mode, zero errors
pytest --cov=src/regin --cov-report=term-missing     # ≥80% overall
```

`loop.py` and `permissions.py` are held at **100%** coverage. They are the two files where a gap
is most likely to cost money or leak a capability.

## Project conventions

These are enforced in review because each one was learned the hard way:

- **`loop.py` stays under 300 lines.** If it grows past that, compaction or permission logic has
  leaked into it. Extract, don't expand.
- **Every tool call goes through the permission layer** — including in tests. There is no bypass.
- **Every model-supplied path goes through `safe_path()`.** No raw `open()` on a path the model
  produced.
- **Every subprocess needs a timeout and output truncation.** No unbounded `bash`.
- **Cost comes from provider `usage` fields**, never from an estimate. If a competitor reports no
  cost, the scorecard says `n/a` rather than `$0`.
- **No real network in unit tests.** Stub at the provider boundary.
- **No agent framework as a foundation.** LangChain, LangGraph, CrewAI, AutoGen, the OpenAI
  Agents SDK, and `claude-agent-sdk` are all out — they own the control flow this project exists
  to build. Libraries at the edges are fine; the rule is about who calls whom.

## Style

Type hints on every public signature. `pathlib`, never string path concatenation — benchmarks run
under WSL2 and Docker. Comment the *why* when it is non-obvious, never the *what*; if a line
needs a comment explaining what it does, rewrite the line.

Test names describe behaviour, not implementation: `test_rejects_a_denied_binary_chained_behind_an_allowed_one`,
not `test_check_allowed_2`.

## Commits and branches

Conventional commits: `feat(bench): ...`, `fix(tools): ...`, `docs: ...`. Branch from `main` as
`feature/<short-description>` or `fix/<short-description>`. Never commit directly to `main`.

Each commit should be one logical change. If the diff needs the word "and" to describe it, it is
probably two commits.

## Reporting bugs

Include the `run_id` and `config_hash` from `regin trace last` — together they pin the exact
configuration a result came from, which is usually the fastest route to a repro.

## Security

Do not open a public issue for a security problem. See [`SECURITY.md`](SECURITY.md).
