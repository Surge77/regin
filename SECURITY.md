# Security Policy

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting:
**Security → Report a vulnerability** on this repository.

Include a reproduction if you have one. I'll acknowledge within a few days.

## What this project's threat model actually is

`regin` executes code that a language model wrote, on your machine, against your repository.
That is the whole point of it, and it means the interesting boundaries are internal. Three
controls carry the weight:

**1. The path jail (`sandbox/paths.py`).** Every model-supplied path goes through `safe_path()`,
which resolves before it checks containment — so `..`, absolute paths, and symlinks pointing
outside the working directory are all caught. A raw `open()` on a model-supplied path anywhere in
the codebase is a bug, regardless of whether it is currently exploitable.

**2. The permission gate (`permissions.py`).** Every tool call passes through it, including in
tests. `--yes` auto-approves and exists for unattended benchmark runs; using it interactively
means you have accepted that the agent can edit files and run commands without asking.

**3. The bash allowlist (`tools/shell.py`).** Commands are tokenised quote-aware, every
operator-separated segment is checked independently, and command substitution (`$(...)`,
backticks, process substitution) is refused outright because it runs a nested command the
allowlist cannot inspect. An empty allowlist means unrestricted and is **only** correct when a
disposable container is the real boundary — that is the benchmark configuration.

Findings that bypass any of these three are in scope and worth reporting.

## Known and accepted limits

- **`--yes` is not a sandbox.** With auto-approval on and no container, the agent can run any
  allowlisted binary against your working directory. Run sweeps in Docker.
- **`--no-docker` on `regin bench run` executes repository test suites directly on the host.**
  The flag says `unsafe` in its help text for that reason. It exists for debugging the harness.
- **The allowlist is a guard, not a sandbox.** `python` is on it, and `python` can do anything.
  It raises the cost of an accident; it does not stop a determined adversarial model. The
  container is the boundary that does.
- **Prompt injection via repository contents is unmitigated.** A file in the repo can contain
  text aimed at the model. The permission gate is what stands between that and an action.

## Secrets

`ANTHROPIC_API_KEY` is read from the environment or `.env`. `.env` is gitignored and `.env.*` is
too. The key is never written to trace records, logs, or run output — run records carry token
counts and costs, not credentials. If you find a key in any artifact this project writes, that is
a vulnerability worth reporting.
