## What this changes

## Why

---

## Does this change agent behaviour?

Behaviour changes are prompts, loop control flow, the tool surface or tool descriptions, and
retry / compaction / context policy.

- [ ] **No** — bug fix, typing, docs, tests, or a behaviour-preserving refactor.
- [ ] **Yes** — the before/after table below is filled in.

### Before / after

Required for behaviour changes. A change that cannot be attributed to a number does not merge.

```
                     before        after
resolve-rate            %     →       %      (n=   , task set:            )
median $/task        $        →    $
p95 latency             s     →       s
mean turns
```

- before `config_hash`:
- after `config_hash`:
- graded with `evalgate` verdicts: yes / no

If you could not run a sweep (it costs real money), say so here and a maintainer will run it.
Do not merge on vibes and backfill the number afterwards.

---

## Checklist

- [ ] `ruff check src tests scorers` passes
- [ ] `pyright` passes (strict, zero errors)
- [ ] `pytest` passes; coverage ≥80%, and 100% on `loop.py` / `permissions.py`
- [ ] New behaviour has a test that fails without the change
- [ ] `loop.py` is still under 300 lines
- [ ] No new tool call bypasses the permission layer
- [ ] No model-supplied path bypasses `safe_path()`
- [ ] No new subprocess without a timeout and output truncation
- [ ] `CHANGELOG.md` updated if user-visible
