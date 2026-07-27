# Architecture

Notes on why the pieces are shaped the way they are. For what the project is and how to run it,
see the [README](../README.md); for the phase plan, [`PLAN.md`](PLAN.md).

## The split that defines the project

```
regin                                    evalgate
─────                                    ────────
the agent loop, tools, sandbox,          golden sets, scorers, judge,
context management, trace emission       scorecard, baseline diff, CI gate

            ──── JSONL run records ────▶
```

`regin` decides how a run terminated. `evalgate` decides whether it resolved anything. Neither
imports the other's internals, and the only shared surface is the run record in `schemas.py`.

That boundary is load-bearing rather than tidy: if the agent could influence its own grade, every
number the project produces would be suspect. The one file that knows both shapes is
`scorers/swebench.py`, which lives here but is installed into evalgate.

## Module map

```
src/regin/
  agent.py          assembly — the single place provider + tools + permissions are wired
  loop.py           the agent loop (hard cap: 300 lines)
  permissions.py    allow / deny / ask gate; every tool call passes through it
  schemas.py        Message, ToolCall, Usage, RunRecord, config_hash
  config.py         regin.toml + REGIN_* env, validated at startup
  trace.py          SQLite session history + JSONL run records
  ui.py             Rich rendering; presentation only

  providers/        the model seam
    base.py         Provider protocol + the pinned pricing table
    anthropic.py    streaming, tool calls, raw usage passthrough, prompt caching

  tools/            the agent's capabilities
    base.py         Tool protocol + registry; Pydantic model → JSON Schema
    fs.py           read / write / anchored edit
    search.py       gitignore-aware glob, ripgrep-backed grep
    shell.py        bash: allowlist, timeout, truncation

  sandbox/
    paths.py        the path jail
    exec.py         Executor protocol: local or Docker

  context/
    compaction.py   summarise-and-continue

  bench/            the measurement half
    task.py         SWE-bench instance model
    workspace.py    per-instance checkout + patch extraction
    budget.py       reserve-before-dispatch spend ceiling
    runner.py       bounded fan-out
    agents.py       regin + Claude Code + Aider, scored identically
    scorecard.py    the comparison table

scorers/swebench.py the evalgate plugin
```

## Decisions worth explaining

### The loop is the deliverable, so nothing else may own it

No LangChain, LangGraph, CrewAI, AutoGen, OpenAI Agents SDK, or `claude-agent-sdk` as a
foundation. Each of them owns the control flow that this project exists to build and measure;
adopting one would make every A/B a measurement of *their* orchestration.

The 300-line cap on `loop.py` enforces this from the other direction. When the loop grows, it is
almost always because compaction, permissions, or rendering leaked into it — all three are seams
that belong outside. The cap turns that drift into a visible failure.

### The provider seam is thin on purpose, and it is not litellm

Cost is the headline metric. Providers report usage in distinct token classes — input, output,
cache-read at 0.1x, cache-write at 1.25x — and an abstraction that flattens those into one number
silently destroys the metric. So `Provider` is a five-method protocol and adapters pass `usage`
through unchanged.

The pricing table is pinned in `providers/base.py` rather than fetched. A silent price change
would retroactively corrupt every historical cost comparison; an outdated pinned table is a
visible, fixable wrong number.

### `effort`, not `temperature`

Sampling parameters are rejected with a 400 on the Claude 5 family. `ModelConfig` therefore
carries `effort` (`low` … `max`) and no sampling knobs. This surprised the config schema during
Phase 1 and is worth knowing before adding a provider.

### Assistant turns replay raw provider blocks

Thinking is on by default on current models, and thinking blocks must be echoed back
byte-identical on the following turn or the request is rejected. A normalised `Message` cannot
reconstruct them, so `Message.provider_blocks` carries the original blocks and the adapter replays
them verbatim. Compaction deliberately drops them — replaying thinking into a fresh summariser
context is invalid.

### Anchored search/replace, not unified diffs

Models emit invalid hunk headers often enough to stall runs. An anchored replacement either matches
exactly once or fails with a message the model can act on: *re-read the file and quote it exactly*.
A recoverable error is worth more than a dead run, which is why `edit_file` distinguishes "not
found" from "matched N times" and says what to do about each.

This is the single highest-leverage detail in the agent. It was called out as the #1 stall risk in
the plan and it earned that.

### Bind-mount the repo; containerise only execution

The Docker backend mounts the checkout into the container and runs `bash` there, while the
filesystem tools keep operating on the host path. Test execution is the part that runs arbitrary
repository code, so it is the part worth isolating. Proxying every read and write through
`docker exec` would buy little and would mean the benchmarked agent no longer matches the shipped
one — which would quietly invalidate the comparison.

### Reserve budget before dispatch

With N instances in flight, accounting for spend only after completion can overshoot the ceiling
by N times the per-task cost before anything notices. `Budget.reserve()` claims headroom first and
`settle()` reconciles against actual cost, so the ceiling holds under concurrency.

### Three independent guards on `bash`

An allowlist on the leading binary of *every* operator-separated segment, a wall-clock timeout that
kills the whole process group, and output truncation before anything reaches the context window.
Each can be defeated alone: a first-token-only check misses `pytest && rm -rf /`; a `proc.kill()`
without the process group orphans the workers `pytest` spawned; untruncated output blows the
context. Command substitution is refused outright, because `echo $(curl evil)` reads as a call to
`echo` no matter how carefully you inspect tokens.

### Honesty guards in the benchmark

The measurement is the product, so the failure modes that matter most are the ones that produce a
flattering wrong number:

- an agent claiming success with an empty diff is downgraded to unresolved,
- checkout failures are skipped rather than scored as agent failures, and excluded from the
  denominator — counting them would reward a crashing harness,
- `bench score` refuses to present a completion rate as a resolve-rate without verdicts,
- an agent that reports no cost shows `n/a`, never `$0`.

## Adding things

**A tool.** Define a Pydantic args model and a class with `name`, `description`, `args_model`, and
`async run()`; register it in `agent.build_tools`. The schema, validation, and wire format all come
from the one model. Keep the tool registration order stable — it is part of the cached prompt
prefix.

**A provider.** Implement the `Provider` protocol, pass `usage` through unflattened, add the model
to `PRICING`. Check whether the family accepts sampling parameters before assuming `effort`.

**A competitor.** Subclass `SubprocessAgent`, implement `_command`, and implement `_parse_cost` only
if the tool emits structured cost. If it does not, leave it — `n/a` is the honest answer.
