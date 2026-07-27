# regin — Project Instructions

> Overrides `~/.claude/CLAUDE.md` and all `~/.claude/rules/*` where they conflict.

## What this is

A terminal coding agent (plan → edit → run tests → iterate) plus the benchmark loop that scores it.
The agent is the cheap half; the measurement is the point. See `docs/PLAN.md`.

Sibling repo: `../evalgate` owns scorers, judge, scorecard, and the CI gate.
`regin` emits JSONL run records; `evalgate` consumes them. Neither imports the other's internals.

## Stack

- **Language:** Python 3.11+. Type gate: `pyright` strict (`pyrightconfig.json`). Tests: `pytest` (`pytest.ini`).
- **CLI:** Typer. **Schemas:** Pydantic v2. **Config:** pydantic-settings + `regin.toml`.
- **Providers:** official SDKs behind a thin `Provider` protocol. No litellm — raw `usage` fields matter.
- **Terminal:** Rich. No Textual.
- **Deps:** pinned exactly in `pyproject.toml`. Secrets via `.env` (never commit).

## Hard rules (project)

- **No agent framework.** LangChain / LangGraph / CrewAI / AutoGen / OpenAI Agents SDK / `claude-agent-sdk`
  are all banned as foundations — they own the control flow that is this project's deliverable.
  Libraries at the edges are fine. `claude-agent-sdk` may be used *only* as a benchmark competitor.
- **`loop.py` stays under 300 lines.** If it grows, compaction or permissions have leaked in — extract them.
- **Every tool call goes through the permission layer.** No exceptions, including in tests.
- **Every file path goes through `safe_path()`.** No raw `open()` on model-supplied paths.
- **No real network in unit tests** — stub at the provider boundary with `respx`.
- **Every subprocess needs a timeout** and output truncation. No unbounded `bash`.
- **Cost accounting comes from provider `usage` fields**, never from an estimate.
- Never commit or push directly to `main`. Branch first.
- POSIX-safe paths only (`pathlib`, never string concat) — benchmarks run under WSL2/Docker.

## Agent routing

| Situation | Use |
|-----------|-----|
| Python code review | `python-reviewer` |
| Tests-first / coverage | `tdd-guide` |
| pyright / build failure | `build-error-resolver` |
| Runtime bug (wrong output) | `debugger` |
| Sandbox, subprocess, path handling, env keys | `security-reviewer` (mandatory) |
| New dep in `pyproject.toml` | `dependency-auditor` (mandatory) |
| Hot path / fan-out concern | `performance-auditor` |

Do not use: non-Python reviewers, `fastapi-reviewer` (no web server), `voltagent-research:*`.

## Discipline

After Phase 2 exists, **no orchestration or prompt change merges without a re-scored before/after.**
A change that cannot be attributed to a number does not go in.
