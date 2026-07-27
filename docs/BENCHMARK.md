# Running the benchmark

This is the half of the project that matters. Everything here exists to produce one defensible
number and to let you move it on purpose.

## The distinction that everything else depends on

There are two different numbers in this system, and conflating them is the fastest way to publish
something false.

**Completion rate** — what `regin bench run` prints. The share of instances where the agent's loop
terminated cleanly rather than hitting the turn limit, the budget, or an error. It measures the
harness. It is useful for smoke tests and says nothing about whether any bug was fixed.

**Resolve-rate** — what `regin bench score --verdicts` prints. The share of instances where the
agent's patch applied, made every `FAIL_TO_PASS` test pass, and broke no `PASS_TO_PASS` test. It
is graded by `evalgate` running `scorers/swebench.py`. This is the number you publish.

`regin bench score` prints a warning whenever it is asked to report the first while looking like
the second. Do not ignore it.

## Prerequisites

SWE-bench is Linux-only in practice — the official evaluation images are `linux/amd64` and the
repositories' test suites assume a POSIX environment. On Windows that means WSL2.

### WSL2 + Docker Desktop (Windows)

Budget half a day the first time. This is the step most likely to eat an afternoon.

```powershell
wsl --install -d Ubuntu-22.04
wsl --set-default-version 2
```

Install [Docker Desktop](https://docs.docker.com/desktop/install/windows-install/), then in
**Settings → Resources → WSL Integration**, enable integration for your distro. Verify from
*inside* WSL, not PowerShell:

```bash
docker run --rm hello-world
```

Then give Docker enough headroom. Test suites for large repositories are memory-hungry and the
default allocation will produce confusing OOM-shaped failures that look like agent failures.
Create `%UserProfile%\.wslconfig`:

```ini
[wsl2]
memory=16GB
processors=8
swap=8GB
```

Restart with `wsl --shutdown`. Disk matters too: the per-instance images are large, and a full
`swe-bench-lite` sweep will pull tens of gigabytes. Keep 100GB free.

**Clone the repository inside the WSL filesystem** (`~/regin`), not under `/mnt/c`. Cross-filesystem
I/O through the Windows mount is slow enough to dominate a sweep's wall-clock.

### Linux / macOS

Docker Engine or Docker Desktop, and that is all. macOS runs `linux/amd64` images under emulation,
which is correct but noticeably slower — fine for a handful of instances, painful for 300.

## Getting a task set

Any JSONL export of SWE-bench works. Field names match upstream exactly, so no conversion step is
needed. Start with `lite` (300 instances) for cost, and report on `verified` (500) once a configuration
is worth the spend. Two helper scripts do this and check it:

```bash
pip install datasets
python scripts/fetch_tasks.py lite                     # -> tasks/swe-bench-lite.jsonl
python scripts/check_tasks.py tasks/swe-bench-lite.jsonl
```

`check_tasks.py` is worth running before every sweep. It catches the failures that would
otherwise surface partway through a paid run, when the money is already gone: instances with no
FAIL_TO_PASS (which can never resolve, so they only drag the denominator down), missing repo or
base_commit (guaranteed skips), empty problem statements, duplicate IDs, and — cheapest and most
important — the reference patch leaking into the prompt.

## Running a sweep

**Always start with `--limit`.** A handful of instances will surface a broken image pull, a bad
key, or a missing binary for a few cents instead of tens of dollars.

```bash
regin bench run \
  --tasks tasks/swe-bench-lite.jsonl \
  --agent regin \
  --limit 5 \
  --max-cost 2 \
  --concurrency 2
```

Then the real sweep:

```bash
regin bench run \
  --tasks tasks/swe-bench-lite.jsonl \
  --agent regin \
  --max-cost 25 \
  --concurrency 8
```

### The spend ceiling

`--max-cost` is enforced *before* each instance is dispatched, not reported afterwards. Headroom
is reserved at dispatch and settled against actual cost on completion, because with N instances in
flight, accounting only after the fact can overshoot by N times the per-task cost before anything
notices.

When the ceiling is reached the sweep stops dispatching, reports how many instances were skipped,
and leaves every record it already collected intact. A sweep that dies at instance 400 still
leaves 399 scoreable records.

Set it deliberately. A three-agent sweep over 300 instances is real money, and the failure mode is
not gradual: a prompt regression that makes every run hit the turn limit multiplies the bill by
the turn cap.

### Concurrency

Each in-flight instance holds a container and an API connection. 4–8 is a reasonable range on a
16GB WSL2 allocation. Higher mostly buys rate-limit errors.

## Scoring

`regin` produces run records; `evalgate` grades them. Install the scorer into evalgate:

```bash
cp scorers/swebench.py ../evalgate/evalgate/scorers/
```

Then grade and render:

```bash
regin bench score runs/bench/*-records.jsonl \
  --verdicts verdicts.json \
  --markdown results.md
```

Record files can be passed positionally (as above), as a repeated `--records` flag, or as a
quoted glob. All three work, because shells disagree about whether a quoted glob reaches the
program unexpanded, and a command copied from these docs should not depend on that.

A verdict counts as resolved only when all three hold:

1. the patch applies cleanly to the repository at `base_commit`,
2. every `FAIL_TO_PASS` test passes,
3. every `PASS_TO_PASS` test still passes.

Rule 3 is what stops "delete the failing test" from scoring as a fix.

## Comparing against competitors

Same task set, same scorer, same checkout:

```bash
regin bench run --tasks tasks/swe-bench-lite.jsonl --agent regin       --max-cost 25
regin bench run --tasks tasks/swe-bench-lite.jsonl --agent claude-code --max-cost 25
regin bench run --tasks tasks/swe-bench-lite.jsonl --agent aider       --max-cost 25

regin bench score runs/bench/*-records.jsonl --verdicts verdicts.json
```

Competitors need their own CLIs installed (`claude`, `aider`). An absent binary is recorded as a
skip with a reason, not scored as a failure.

**On cost comparability:** Claude Code reports structured usage and cost, so its column is
directly comparable to regin's. Aider does not, so its cost column reads `n/a` rather than a
parsed-from-prose guess that would break on the next release. Resolve-rate, latency, and turns
remain fully comparable across all three.

## Running an A/B

This is the workflow the project exists for.

1. Score the current configuration. Note the `config_hash` from the scorecard.
2. Make exactly one change — a prompt, a tool description, a retry policy.
3. Re-run on the **same** task set with the same `--limit` and seed conditions.
4. Score both. The scorecard groups by `(agent, config_hash)`, so both configurations appear as
   separate rows in one table.

Change one thing at a time. Two simultaneous changes give you a number you cannot attribute, which
is worth less than no number at all.

## Reading the scorecard

```
agent        config     resolve    n     median $   total $   p95 latency   mean turns
regin        a3f9c201   34.7%      300   $0.142     $47.31    181s          9.8
claude-code  b81e0d44   41.2%      300   $0.310     $98.20    240s          14.1
aider        c02f7719   28.9%      300   n/a        n/a       155s          7.2
```

- **median $/task**, not mean. Cost is long-tailed; a handful of turn-limit runs dominate a mean
  and make two configurations look different when their typical behaviour is identical.
- **p95 latency**, nearest-rank. At n=20 that is the 19th value, so one slow run does not set it.
  The definition is stated because it genuinely changes the figure at small n.
- **config** is the first 8 characters of `config_hash`, which pins the exact model, loop, and
  tool configuration a row came from.

## Troubleshooting

**Every instance skips with a workspace error.** The runner clones from GitHub through a local
bare mirror. Check network access and that `git` is on `PATH` inside WSL.

**Image pull failures.** The per-instance images follow the official naming
(`swebench/sweb.eval.x86_64.<instance>:latest`). Not every instance has a published image; those
show up as skips.

**Instances fail that should pass, with OOM-shaped errors.** Raise the WSL2 memory allocation.

**The sweep is far slower than expected.** Check that the repository is in the WSL filesystem, not
under `/mnt/c`.

**Costs higher than expected.** Look at `mean turns` first. A high mean means runs are grinding
against the turn limit, which is usually a prompt or tool-description problem rather than a model
problem.
