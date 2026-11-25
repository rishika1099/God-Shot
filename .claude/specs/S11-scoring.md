# S11: Scoring, metrics and charts

- **Owner:** Siva (`score.py`), Rishika (charts in `report.ipynb`)
- **Status:** in progress (charts notebook + score.py + run_all.py done; numbers need dev/test runs)
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
- 2026-11-21 (Rishika): Built `eval/report.ipynb` with all chart & report cells (clip-cluster 95% bootstrap CIs, two-sided exact paired sign test, grouped bars by label type / CameraBench vs own clips / difficulty, 5-bin reliability diagram + ECE + Brier, mirrored-control yes-bias, selective accuracy, rounds-to-converge + latency, and `eval/results.md` + `eval/failures.md` exporters). Ready to run on `dev` by 3:45 PM and `test` after the >= 4:00 PM freeze.
- 2026-11-21 (Siva): `eval/score.py`, `eval/run_all.py`, `tests/test_score.py` (24 tests). Branch `siva/s11-score`
  (stacked on `siva/s03-geometry`).
  - `score_scene(scene, label)` → `[{label_id, type, pred, truth, correct, unresolved}]`, the fields
    `eval/report.ipynb` reads. String values are lower-cased before comparing.
    - SIDE: `screen_x` < 0.5 → "left". If the scene puts the subject outside the FOV or `visible=false`, pred is
      `"offscreen"` and counts as wrong (humans only label SIDE for visible things).
    - A_LEFT_OF_B: `screen_x` order. CLOSER: distance → "a"/"b". TOWARD: distance at t1 < at t0.
    - CAM_TRANSLATION / CAM_ROTATION: `geometry.camera_motion(t0, t1)` + the S04 thresholds (move > 15% of mean
      scene distance, pan > 10°). Truck uses the same 15% rule as dolly (S04 has no truck template). Arc = a lateral
      move with a pan the opposite way on screen (arc_left = moves left + pans right), matching the convention S10
      verified on CameraBench. Dominant axis wins between dolly and truck/arc. A floor of 1.0 room unit on the mean
      scene distance keeps a near-empty scene from turning jitter into a "move".
    - Entities resolve through `entity_map` ONLY; a miss → `correct=false`, `unresolved=true`.
  - `verifier_vs_human(labels, clips, frames_root)` asks each askable label as the matching S04 question
    (SIDE → SIDE_LEFT, A_LEFT_OF_B, CLOSER, TOWARD, CAM_ROTATION pan_* → PAN, CAM_TRANSLATION dolly_* → DOLLY).
    Not askable: rotation "none", truck/arc/static. On the 40 CameraBench test clips that is 23 of 47 labels.
    Wording comes from S04's question builder (`godshot.checks.build_question`, or `_build_question` until it is
    made public) so humans and the pipeline are compared on identical questions. Writes
    `eval/runs/verifier_vs_human_<split>.json` with `p_yes`, `truth_bool`, `correct` (+ observed, uncertain).
    Pair times are clamped inside the clip (`duration_s` from clips.csv) because CameraBench labels span the clip.
  - CLI: `python eval/score.py summary --split dev` (baseline vs full accuracy in the terminal),
    `python eval/score.py vvh --split dev` (needs API key + ffmpeg).
  - `run_all.py --split --system baseline|full [--force]`: 4 clips in parallel; baseline = S02 `extract.baseline`
    → `scene_r0.json` + loop_log round 0 (tokens, latency), full = S06 `pipeline.run`. Skips clips that already have output; a failing clip is logged and
    reported, never stops the rest; per-split report in `eval/runs/<system>/_run_<split>.json`.
  - Dry run: Rishika's 40 CameraBench test labels (`rishika/s10-eval-data`) load and score end to end.
  - Still open: real numbers need S02 + S06 merged, clips downloaded to `eval/clips/`, and own-clip layout labels.
- 2026-11-25 (Rishika): `summarize_runs` compares baseline and full only on clips that have both runs (reports `unpaired_skipped`). Boolean label values given as strings ("false") are parsed correctly in `askable` and `score_scene`.
