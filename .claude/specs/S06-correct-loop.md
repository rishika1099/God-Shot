# S06: Correct + loop controller

- **Owner:** Prajesh
- **Status:** todo
- **Depends on:** S02, S05 (build against `mocks/checks_mock.json` until S05 lands)
- **Target:** 3:30 (M1)
- **Models:** `MODEL_CORRECT` = gemini-4-argon, `thinking_level=HIGH`; same video URI + failing frames as high-res image parts; JSON schema enforced

## Goal
Use failed checks to fix the scene, keep a fix only if it helps, and mark what still fails as low-confidence.

## Files
- `godshot/correct.py`
- `godshot/prompts/correct.md`
- `godshot/pipeline.py` (`run(clip) -> SceneV1`, used by the CLI `run` and by the web UI)
- `tests/test_loop.py` (with fake extract/verify functions)

## Spec
Loop controller:
```
scene0 = extract(clip)                         # baseline
for r in 1..MAX_ROUNDS:
    checks = verify(make_checks(scene_{r-1}))
    if no fails: break
    cand = correct(scene_{r-1}, failed, passed)
    cand_checks = verify(make_checks(cand))
    accept if pass_rate(cand) > pass_rate(prev) and regressions <= 1   # regressions = checks with the same id that passed before and fail now
    else break
mark_low_confidence(final, still_failing)       # merge by entity + time window
```
- Correction prompt (`prompts/correct.md`): failed checks table (id, template, mmss, instruction, expected, p), compact list of passed checks to KEEP, attached frames (label "Image k is at MM:SS.sss"). Ask for a FULL corrected JSON, same ids, the smallest changes; in `model_notes` list "entity @ t: old → new because check cid". If something can't be resolved, lower its confidence.
- Write `scene_r{n}.json`, `checks_r{n}.json`, `scene_final.json`, `loop_log.json` (per round: pass_rate, n_checks, n_fail, n_uncertain, regressions, accepted, changes, tokens, latency_s).
- CLI: `python -m godshot run clip.mp4 -o out/<id>` → also calls S07/S08 if available.

## Acceptance criteria
- [ ] With fake functions, the loop stops on: no fails / max rounds / no improvement; it never accepts a regression > 1.
- [ ] On 1 real dev clip: ≥1 round runs; loop_log shows pass rate per round; low_confidence gets filled.
- [ ] Total runtime for a 10 s clip < 3 min (log it).

## Notes / changelog
-
