# 🌤️ Overview

---

### Description

**regin** — a terminal coding agent that reforges broken code: plan → edit → run tests → iterate,
scored on a held-out benchmark by [`evalgate`](../../evalgate).

The agent is the cheap half. The measurement is the moat. Every orchestration or prompt change
must be attributable to a number: resolve-rate, cost per task, latency, turns.

Sibling repo split (per Notion build order — "build the harness once, point it at both"):

| Repo | Owns |
|------|------|
| `evalgate` | golden sets, scorers, judge, scorecard, baseline diff, CI gate |
| `regin` | the agent loop, tools, context management, sandbox, trace emission |

`regin` emits JSONL run records; `evalgate` consumes them via a `swebench` scorer plugin.
Neither imports the other's internals.

### Completion Criteria

- [ ] `regin fix <task>` resolves a failing test in a real repo without human intervention
- [ ] README states a number: "resolves X% of N held-out SWE-bench-verified tasks"
- [ ] Same harness scores `regin`, Claude Code, and Aider on the identical task set
- [ ] At least one documented A/B: "changed <thing>, resolve-rate X% → Y%"
- [ ] Cost + latency + turns reported per run, sourced from provider `usage` fields
- [ ] pyright strict clean, pytest ≥ 80% coverage, 100% on `loop.py` + `permissions.py`
- [ ] Published to PyPI; `uvx regin --help` works on a clean machine

---

# 📝 Phases

---

## Phase 0 — Scaffold  ·  ~1 day  ·  STATUS: done

**Goal:** repo skeleton that type-checks and tests green with zero features.

| Part | Deliverable |
|------|-------------|
| 0.1 | `pyproject.toml`, pinned deps, `regin` console script |
| 0.2 | `pyrightconfig.json` strict, `pytest.ini`, `ruff` config |
| 0.3 | Package layout under `src/regin/` |
| 0.4 | GitHub Actions: ruff → pyright → pytest → coverage |
| 0.5 | `.env.example`, `regin.toml`, `.gitignore` |

**Exit:** `pytest` green, `pyright` clean, CI passing on a branch.

---

## Phase 1 — v0 Agent Loop  ·  ~1 week

**Goal:** the smallest thing that is honestly an agent. Closes the feedback edge.

| Part | Deliverable | Notes |
|------|-------------|-------|
| 1.1 | `Provider` protocol + Anthropic adapter | streaming, tool-calls, raw `usage` passthrough incl. cache tokens |
| 1.2 | Tool registry, Pydantic-schema'd | `model_json_schema()` → provider tool payload; one source of truth |
| 1.3 | `read_file`, `write_file`, `glob` | cwd jail via `safe_path`; gitignore-aware globbing |
| 1.4 | `edit_file` — anchored search/replace | NOT model-generated unified diffs; failed match = recoverable error fed back |
| 1.5 | `grep` via ripgrep subprocess | `--json` output, respects `.gitignore` |
| 1.6 | `bash` tool | hard timeout, output truncation, cwd pinned, allowlist |
| 1.7 | `loop.py` — the agent loop | ≤ 300 lines. If it exceeds, compaction/permissions have leaked in — extract |
| 1.8 | `permissions.py` | allow / deny / ask, plus `--yes` auto-approve for benchmark runs |
| 1.9 | `trace.py` | SQLite session history + JSONL run record + `config_hash` |
| 1.10 | Rich streaming renderer | syntax-highlighted diffs, live turn status |

**Exit:** point `regin` at a small repo with one failing test; it reads, edits, runs pytest,
reads the failure, re-edits, and goes green — unattended.

**Risks:** `edit_file` match failures are the #1 cause of agent stalls. Budget time to tune
the anchoring strategy (exact → whitespace-normalised → fuzzy fallback, in that order).

---

## Phase 2 — Benchmark Loop  ·  ~2 weeks  ·  ⭐ the actual deliverable

**Goal:** a defensible number, and the ability to move it on purpose.

| Part | Deliverable | Notes |
|------|-------------|-------|
| 2.1 | Docker sandbox backend | `docker` SDK, one container per instance, matches official harness |
| 2.2 | WSL2 + Docker Desktop setup documented | SWE-bench is Linux-only in practice; budget half a day |
| 2.3 | `regin bench run` — task loader + fan-out | bounded concurrency, hard $ ceiling before first full sweep |
| 2.4 | `evalgate` scorer plugin: `swebench` | patch applies? tests pass? → resolve-rate |
| 2.5 | Baseline competitors | Claude Code (`claude-agent-sdk`) + Aider, same task set, same scorer |
| 2.6 | Scorecard | resolve-rate, median $/task, p95 latency, mean turns — per agent, per config |
| 2.7 | **Publish the number** | README + writeup. Project becomes a portfolio piece here, not at Phase 3 |

**Exit:** one table comparing three agents on N held-out tasks with cost attached.

**Risks:** a 3-agent × 500-task sweep is real money. Set `max_run_cost_usd` in `regin.toml`
and enforce it in the runner *before* the first sweep, not after the bill.

---

## Phase 3 — Hardening  ·  ~3–4 weeks

**Goal:** every feature added is now evidence, because Phase 2 gave you a baseline.

| Part | Deliverable | Expected claim |
|------|-------------|----------------|
| 3.1 | Context compaction (summarise-and-continue) | "lifted resolve-rate X% → Y% on long tasks" |
| 3.2 | tree-sitter repo outline instead of file dumps | "cut mean input tokens N%" |
| 3.3 | Subagents (fresh-context bounded search) | "cut context overflow failures N%" |
| 3.4 | Planning pass before edits | A/B the planner prompt — the Notion headline example |
| 3.5 | Retry/recovery policy tuning | "reduced stalls from N% to M%" |
| 3.6 | Prompt caching | "cut cost N% at flat resolve-rate" |

**Rule:** no change merges without a re-scored before/after. That's the whole discipline.

---

## Phase 4 — Ecosystem *(optional)*  ·  ~2 weeks

| Part | Deliverable |
|------|-------------|
| 4.1 | MCP client (`mcp` Python SDK) |
| 4.2 | Pre/post-tool hooks |
| 4.3 | Session persistence + resume |
| 4.4 | **DSPy** prompt optimisation against the harness metric — "automated optimisation lifted X% → Y%" |

4.4 is the strongest single upgrade available and is worthless before Phase 2. Do not reorder.

---

# 📝 Notes

---

### Explicitly out of scope

Full TUI with panes, plugin marketplace, IDE extensions, web/desktop clients, git integration
beyond the `bash` tool, worktree isolation. None of these produce a number.

### Not using, deliberately

LangChain, LangGraph, CrewAI, AutoGen, OpenAI Agents SDK, `claude-agent-sdk` *as a foundation*.
All own the control flow that is the deliverable. `claude-agent-sdk` is used as a **baseline
competitor** in Phase 2 instead.

Libraries at the edges are fine and encouraged — the rule is about who calls whom.

### Naming

`regin` — the Norse smith who reforged the shattered sword Gram from its shards.
Verified 2026-07-27: free on PyPI, effectively no GitHub collision.
(Predecessor `helix-cli` collided with the Helix editor and Perforce Helix Core — archived, not deleted.)

### Open questions

- [ ] npm / trademark check on "regin" before first public release
- [ ] Which SWE-bench split — `verified` (500) vs `lite` (300)? Start `lite` for cost, report on `verified`
- [ ] Modal for parallel sweeps, or accept slow serial Docker runs?
