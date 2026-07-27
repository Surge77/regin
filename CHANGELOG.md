# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — Phase 4: ecosystem

- **Hooks** (`hooks.py`) — pre- and post-tool hooks. A pre-hook may veto a call; neither kind can
  rewrite one, so the call the model made and the call that ran are always the same. Hooks run
  *inside* the permission gate, so a hook can veto but never approve. A broken pre-hook fails
  closed; a broken post-hook is swallowed, because the tool already ran.
- **Session persistence and resume** (`session.py`, `regin fix --resume`, `regin session list`) —
  a run stopped at the turn or budget limit can be continued instead of restarted. Resume is
  refused when `config_hash` differs: continuing under a different prompt or model blends two
  configurations and makes the result attributable to neither.
- **MCP client** (`tools/mcp.py`, extra: `regin[mcp]`) — third-party tools adapted into the same
  registry, so the loop needs no special case. Names are prefixed `mcp__` so a remote tool cannot
  shadow a built-in one, and the server's own JSON Schema is passed through verbatim rather than
  re-derived.

**Not built: DSPy prompt optimisation (4.4).** The plan states it is worthless before Phase 2 is
scored and says explicitly not to reorder. Optimising against a metric requires a measured
baseline to optimise against; building it now would produce an unfalsifiable claim. It stays open
until a sweep has been run.

### Added — Phase 3: hardening

Every experiment ships **disabled**, because the project's rule is that no orchestration change
merges without a re-scored before/after. The shipped default remains the measured baseline, and
each flag is one clean A/B.

- **Planning pass** (`loop.plan_first`) — one planning turn before any edit, injected as an
  assistant turn rather than a system one so the model revises it when evidence disagrees.
  Planner effort is its own knob.
- **Repository outline** (`loop.outline_tool`) — signatures instead of whole files, via stdlib
  `ast`. Exact for Python, no new dependency.
- **Search subagent** (`loop.subagent_tool`) — bounded, read-only, its own context, no recursive
  delegation. It cannot edit or run commands: otherwise "delegate the search" becomes "delegate
  the work".
- **Retry** (`loop.retry_attempts`, on by default) — bounded jittered backoff on transient
  failures only. This corrects a measurement error rather than changing behaviour: a rate limit
  mid-sweep would otherwise be recorded as an agent failure. A 400 is never retried.
- Prompt caching (3.6) already shipped in Phase 1.

### Added — Phase 2: benchmark loop

- `regin bench run` — sweep an agent across a SWE-bench task set with bounded concurrency and a
  hard spend ceiling enforced before each dispatch.
- `regin bench score` — aggregate run records into the comparison table; `--markdown` writes the
  README table.
- Docker sandbox backend. The checkout is bind-mounted and networking disabled, so the agent edits
  on the host while tests run in a disposable container against the same bytes.
- `scorers/swebench.py` — the evalgate scorer plugin. An instance resolves only when the patch
  applies, every `FAIL_TO_PASS` passes, and no `PASS_TO_PASS` regresses.
- Claude Code and Aider as benchmark competitors, scored on the same task set with the same
  scorer. `claude-agent-sdk` is a competitor here and nowhere else.
- Per-instance workspace preparation via a local bare mirror, and patch extraction that stages
  untracked files so a fix delivered as a new file is not lost.

### Added — Phase 1: the agent loop

- `regin fix` — reads, edits, runs tests, reads the failure, re-edits, and goes green unattended.
- Anthropic provider adapter: streaming, tool calls, raw `usage` passthrough including cache token
  classes, and prompt caching over the tools + system prefix.
- Tools: `read_file`, `write_file`, anchored `edit_file`, gitignore-aware `glob`, ripgrep-backed
  `grep`, and `bash`.
- Summarise-and-continue context compaction, extracted from the loop.
- Rich streaming renderer with a per-run cost, latency, and turn summary.

### Changed

- `model.temperature` → `model.effort`. Sampling parameters are rejected with a 400 on the Claude
  5 family; `effort` (`low` … `max`) is the depth control.
- Assistant turns now round-trip raw provider content blocks. Thinking is on by default and
  thinking blocks must be echoed back byte-identical, which a normalised message cannot
  reconstruct.

### Fixed

Three `bash` allowlist bypasses, all found while writing its tests:

- naive separator splitting rejected `python -c "import os; print(x)"`, where the semicolon is
  data rather than a command separator — now tokenised quote-aware;
- `pytest && rm -rf /` passed a check that only inspected the first token — every
  operator-separated segment is now checked independently;
- `echo $(curl evil)` bypassed the allowlist entirely, since it reads as a call to `echo` —
  command substitution is now refused outright.

Also: `proc.kill()` on timeout orphaned processes spawned by the killed command, so the timeout
never actually reclaimed the run. Commands now start in their own process group.

## [0.1.0] — Phase 0

### Added

- Package scaffold, pinned dependencies, `regin` console script.
- pyright strict, pytest, ruff, and GitHub Actions CI.
- Path jail (`safe_path`), permission gate, run-record schemas, SQLite + JSONL trace store.
