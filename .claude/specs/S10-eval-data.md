# S10: Eval data (CameraBench subset + clips + human labels)

- **Owner:** Rishika (everyone labels for 20 minutes at ~1:30)
- **Status:** todo
- **Depends on:** S00 (`label.v1` schema)
- **Target:** 4:00 (dev split ready by 1:30; test split complete by 4:00)
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
- [ ] By 1:30: 5 dev clips labeled.
- [ ] By 4:00: ≥ 20 own test clips labeled + 40 CameraBench clips mapped; `labels.jsonl` validates against `LabelV1`.
- [ ] `CREDITS.md` lists every non-own clip with its license; consent is collected for every venue clip.

## Notes / changelog
-
