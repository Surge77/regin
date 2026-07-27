---
name: Benchmark regression
about: A change moved the number the wrong way
labels: benchmark, regression
---

## The numbers

```
                     before        after
resolve-rate            %     →       %      (n=   , task set:            )
median $/task        $        →    $
p95 latency             s     →       s
mean turns
```

- before `config_hash`:
- after `config_hash`:

## What changed between the two runs

One change per report where possible — two simultaneous changes give a number that cannot be
attributed.

## Task set and conditions

- task set file:
- `--limit`:
- `--concurrency`:
- sweep stopped early (budget)? yes / no
- instances skipped:

## Graded or ungraded

- [ ] Scored with `evalgate` verdicts (a real resolve-rate)
- [ ] Completion rate only (not a resolve-rate — see `docs/BENCHMARK.md`)

## Instances that regressed

If you have per-instance verdicts, the ones that flipped from resolved to unresolved are the most
useful thing in this report.
