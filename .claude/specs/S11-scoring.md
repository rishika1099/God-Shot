# S11: Scoring, metrics and charts

- **Owner:** Siva (`score.py`), Rishika (charts in `report.ipynb`)
- **Status:** todo
- **Depends on:** S03, S10, S06 (start against mocks + the first baseline runs)
- **Models:** none for scoring; `MODEL_VERIFY` only for the verifier-vs-human metric

## Goal
Turn runs + human labels into the slide numbers: baseline vs loop, verifier accuracy, calibration, selective accuracy, failures.

## Files
- `eval/score.py`, `eval/run_all.py` (runs baseline + full on every clip in a split, in parallel, cached)
- `eval/report.ipynb` → `eval/results.md` + `eval/figs/*.png`
- `eval/failures.md`

## Spec
- `score_scene(scene, label) -> list[{"label_id","type","pred","truth","correct"}]`: evaluate each human label with `geometry.py` on the scene (SIDE via the screen_x sign; A_LEFT_OF_B via screen_x order; CLOSER via distance; TOWARD via distance change; CAM_* via `camera_motion` over [t0,t1] with the same thresholds as S04). If an entity can't be resolved via `entity_map` → `correct=false` (count it as a miss, report it separately).
- `run_all.py --split test --system baseline|full`: writes `eval/runs/<system>/<clip_id>/...`.
- Metrics (see `.claude/docs/eval.md`): per-type accuracy with 95% bootstrap CIs (by clip, 1000 resamples), paired sign test, verifier-vs-human accuracy (ask each human label as a Decisions question via `verify.ask`), reliability diagram + ECE + Brier, selective accuracy (high vs low confidence), rounds-to-converge, regressions, % of clips where the loop hurt, cost/latency.
- Report CameraBench (motion only) and own clips (layout + motion) as separate groups, and by difficulty tag.
- `failures.md`: every failed label with a thumbnail (from S01) and the scene's claim; pick 4 for the gallery.

## Acceptance criteria
- [ ] `python eval/run_all.py --split dev` + `score.py` produce `results.md` on the dev split.
- [ ] Test-split numbers produced only after prompts and thresholds are frozen, with the freeze recorded in results.md.
- [ ] Every number on the slides traces to `results.md`.

## Notes / changelog
-
