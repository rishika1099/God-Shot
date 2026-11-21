# S10: Eval data (CameraBench subset + clips + human labels)

- **Owner:** Rishika (everyone helps label)
- **Status:** in progress (40 CameraBench mapped + 11 Blender clips trimmed; human labels next)
- **Depends on:** S00 (`label.v1` schema)
- **Models:** none

## Goal
Inputs we didn't hand-pick to pass, with human labels written before seeing model output. This is the "proof" judges look for.

## Files
- `eval/camerabench/fetch.py`: downloads N clips + labels from HF `syCen/CameraBench`, maps tags to our enums
- `eval/clips/` (gitignored videos), `eval/clips.csv` (id, source, license, split, difficulty, duration, path)
- `eval/labels/labels.jsonl` (`label.v1`)
- `eval/label_tool.py`: tiny CLI that shows frames at t and prompts for labels (optional; a spreadsheet → JSONL converter is fine)
- `eval/CREDITS.md`, `eval/consent/` (signed forms; gitignored if they contain personal info)

## Spec
- **CameraBench:** sample 40 clips (seed 0), 5–15 s, with at least one translation or rotation tag. Map: `dolly-in→dolly_in`, `dolly-out→dolly_out`, `truck-left/right→truck_left/right`, `pan-left/right→pan_left/right`, `arc-CW→arc_right`, `arc-CCW→arc_left` (VERIFY the orientation convention in their taxonomy), no-motion→`static`; ignore tilt/roll/zoom/shake tags. These give clip-level `CAM_TRANSLATION`/`CAM_ROTATION` labels over the whole clip. Check the dataset license; numbers only, don't show its clips in the demo.
- **Blender open movies:** Tears of Steel (CC BY 3.0) live-action shots, plus Sintel/Spring/Charge (CC BY) as a domain-shift probe; 10–12 single continuous shots of 5–12 s. Trim with S01 `trim`.
- **Pexels:** 4–6 clips (free license; don't use people in product/shoppable contexts).
- **Venue clips:** 8–10 clips filmed by NON-team people with signed consent; ask for deliberate moves (dolly in, pan, walk-and-talk, arc) + one each of whip pan, crowd, low light, a 30 s long take. Tape a floor grid for 3 of them and record camera start/end positions.
- **Labels:** 6–10 per clip (types: CAM_TRANSLATION, CAM_ROTATION, SIDE, A_LEFT_OF_B, CLOSER, TOWARD), times rounded to 0.5 s, written BEFORE running the model. Fill `entity_map` after the baseline run. Double-label 25% of clips.
- **Split:** 5 dev clips (mixed sources), everything else test. Never tune on test.

## Acceptance criteria
- [ ] 5 dev clips labeled (needed first, for tuning).
- [ ] ≥ 20 own test clips labeled + 40 CameraBench clips mapped; `labels.jsonl` validates against `LabelV1`.
- [ ] `CREDITS.md` lists every non-own clip with its license; consent is collected for every venue clip.

## Notes / changelog
- 2026-11-21: Implemented `eval/camerabench/fetch.py`, `eval/label_tool.py` (`template`, `import-csv`, `interactive`, `fill-entity-map`, `validate`), `eval/CREDITS.md`, and `eval/consent/CONSENT_TEMPLATE.md`.
- **Verified CameraBench taxonomy & license (`syCen/CameraBench`, CC BY 4.0):** Co-occurrence across all 1,071 `test.jsonl` clips proves `arc-CW` co-occurs exclusively with `truck-left` (6) + `pan-right` (9) → mapped to `arc_left`, and `arc-CCW` co-occurs exclusively with `truck-right` (11) + `pan-left` (15) → mapped to `arc_right` (opposite of the unverified placeholder in the spec).
- Fetched 40 CameraBench clips (seed 0, 5–15 s, GIF→MP4 in gitignored `eval/clips/cb_001..040.mp4`) and trimmed 11 zero-cut Blender Open Movie clips (`tos_001`–`010` + `sintel_001`: 5 `dev` + 6 `test`) into `eval/clips/` + `eval/clips.csv` + `eval/CREDITS.md`.
- Confirmed intentional: `eval/camerabench/labels.jsonl` stores the standalone 40-clip CameraBench subset, and `eval/labels/labels.jsonl` is the unified label file (currently those 40 CameraBench clips; own `dev`/`test` human labels merge into it via `eval/label_tool.py import-csv`).
- Remaining: human-label the 5 `dev` clips first (for tuning), then collect & label >= 20 own `test` clips (Pexels + non-team venue clips w/ signed consent + 3 taped-grid clips, 25% double-labeled).
