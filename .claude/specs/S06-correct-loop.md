# S06: Correct + loop controller

- **Owner:** Rishika
- **Status:** done
- **Depends on:** S02, S05 (build against `mocks/checks_mock.json` until S05 lands)
- **Models:** `MODEL_CORRECT` = gemini-4-argon, `thinking_level=HIGH`; same video URI + failing frames as high-res image parts; JSON schema enforced

## Goal
Use failed checks to fix the scene, keep a fix only if it helps, and mark what still fails as low-confidence.

## Files
- `godshot/correct.py`
- `godshot/prompts/correct.md`
- `godshot/pipeline.py` (`run(clip) -> SceneV1`, used by the CLI `run` and by the web UI)
- `tests/test_loop.py` (with fake extract/verify functions)

## Spec
### Why a shared question bank
Scoring each scene on its own `make_checks()` output lets a "correction" raise its score without fixing anything: it can make failing checks disappear (move the wrong object into an abstain margin, delete it, set `visible=false`, or make the camera static so no PAN/DOLLY checks are emitted). Also, SIDE_LEFT and SIDE_RIGHT have different `check_id`s, so flipping an object's side would make the old failing check vanish instead of counting as a regression. So every scene in a clip is scored on ONE shared, growing set of questions.

### State per clip
- `bank: dict[QKey, CheckV1]`: ACCUMULATES across all rounds: prev + every candidate's `make_checks()`. Never shrinks. Key = `schema.qkey(c)`.
- `answers: dict[QKey, Raw]`: raw verifier answers (S05). Verify only the bank questions not yet in `answers`.

### Scoring (`score(scene, bank, answers)`)
```
usable = [k for k in bank if observed(answers[k]) is not None]      # S05 observed()
hits   = {k for k in usable if predict(scene, bank[k]) == observed(answers[k])}   # S04 predict()
                                                                     # predict() → None (abstain) is a MISS
score  = len(hits) / max(len(usable), 1)
```

### Loop controller
```
scene0 = extract(clip)                                   # baseline (S02)
bank, answers = {}, {}
add make_checks(scene0) to bank; answers |= verify(unanswered(bank))
for r in 1..MAX_ROUNDS:
    prev = scene_{r-1}
    failing = [k in usable if k not in hits(prev)]
    if not failing: break                                 # no fails
    cand = correct(prev, failing, passing)                # Argon
    cand = freeze_identity(cand, prev)                    # restore ids + visual_descriptions from prev
    add make_checks(cand) to bank; answers |= verify(unanswered(bank))
    s_prev, s_cand = score(prev), score(cand)             # BOTH on the same, updated bank
    regressions = hits(prev) - hits(cand)
    accept cand iff s_cand > s_prev and len(regressions) <= 1
    if not accepted: break                                # no improvement
mark_low_confidence(final, still_failing)                 # merge by entity + time window
```
- **Frozen identity (`freeze_identity`)**: entity ids and `visual_description`s must be identical across rounds (questions embed the descriptions, so qkeys depend on them). If a candidate renames an id or changes a description, restore it from prev, matching by id first, then by description, then by type + nearest position. A candidate entity with no match in prev is a new entity (allowed, within the ≤6 objects / ≤3 actors limits). An entity in prev that's missing from cand is deleted. Never "un-delete" it: deletion is penalised by scoring because `predict()` returns `None`.
- Correction prompt (`prompts/correct.md`): failed checks table (id, template, mmss, instruction, expected-by-prev, observed, p), compact list of passed checks to KEEP, attached frames (label "Image k is at MM:SS.sss"). Ask for a FULL corrected JSON, same ids, same visual_descriptions, the smallest changes; in `model_notes` list "entity @ t: old → new because check cid". If something can't be resolved, lower its confidence.
- Write `scene_r{n}.json`, `checks_r{n}.json` (the whole bank with `result` = raw + verdict relative to the round's accepted scene, via S05 `attach`), `scene_final.json`, `loop_log.json`.
- `loop_log.json`, per round: `bank_size`, `usable`, `score_prev`, `score_cand`, `regressions` (count + qkeys/check_ids), `accepted`, `changes`, `tokens`, `latency_s`.
- CLI: `python -m godshot run clip.mp4 -o out/<id>` → also calls S07/S08 if available.

## Acceptance criteria
- [x] With fake functions, the loop stops on: no fails / max rounds / no improvement; it never accepts more than 1 regression.
- [x] Shared-bank tests in `tests/test_loop.py` (fake verify + fake/real geometry):
  - [x] a) a candidate that only moves the wrong object into the abstain zone → REJECTED
  - [x] b) a candidate that deletes the wrong object → REJECTED
  - [x] c) a candidate that flips the object to the correct side → ACCEPTED
  - [x] d) a candidate that fixes 3 checks but breaks 2 previously correct ones → REJECTED
  - [x] a candidate that renames an id or rewrites a visual_description gets it restored from prev before scoring
  - [x] the bank only grows, and no qkey is verified twice
- [x] On 1 real dev clip: ≥1 round runs; loop_log shows bank_size, usable, score_prev, score_cand, regressions, accepted per round; low_confidence gets filled.
- [x] Total runtime for a 10 s clip logged (see Notes: HIGH r1 = 266 s / 94.4%, MEDIUM = 34 s / 82.4%; adaptive HIGH/MEDIUM avoids 464 s r2 tail).

## Notes / changelog
- 2026-11-21: Implemented `godshot/prompts/correct.md`, `godshot/correct.py` (`correct`, `freeze_identity`, `mark_low_confidence`, `build_correction_prompt`), `godshot/pipeline.py` (`run`, `run_loop`, `score_bank`), and `tests/test_loop.py` (merged as PR #2).
- 2026-11-21 (live dev run on `eval/clips/tos_001.mp4`, 9.0 s *Tears of Steel*, CC BY 3.0):
  - Round 0 (baseline): `77.6%` (`38/49` usable; `44.9%` on Round 1 bank).
  - Round 1 (Argon `HIGH`): `44.9% → 94.4%` (`84/89`, `0` regressions, `accepted=True`, `40,132` tokens, `266 s`).
  - Round 2 (Argon `HIGH`): `93.3% → 100.0%` (`90/90`, `0` regressions, `accepted=True`, auto-stopped before Round 3 with 0 fails). Rendered full 9:16 `out/tos_001/reel.mp4` (`map.mp4` 0.5 s + `reel.mp4` 0.4 s).
- 2026-11-21 (dev tuning follow-up, `rishika/s06-dev-tuning`):
  - Prompt tuning (`godshot/prompts/correct.md`): added physical camera-smoothness rule (prevents 0.5–1.5 s dolly zig-zags seen on stress test) and early/late off-screen object positioning guidance.
  - Robustness (`godshot/correct.py`): empty-reply retry (retries main call instead of repairing `""`), `_validate_scene_json` pins `prev.clip` so repair calls cannot zero `duration_s`, explicit `KEEP A closer than B` / `KEEP A left of B` passed-check constraints.
  - `HIGH` vs `MEDIUM` ablation on `tos_001`: `MEDIUM` ran 7.5× faster (`33.9 s`, `21.5k` tokens) but caused 5 `CLOSER` regressions when scaling `x` inward (`44.0% → 82.4%`, rejected), whereas `HIGH` widened `hfov_deg` cleanly (`0` regressions, `100%`). Switched to adaptive thinking (`HIGH` for `>5` failures, `MEDIUM` for `<=5` cleanup failures).
