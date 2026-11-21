# Contracts (FROZEN after S00)

These are the shared interfaces. Pydantic models live in `godshot/schema.py`. Additive OPTIONAL fields are allowed only with a dated note at the bottom of this file. Never rename or remove fields.

## Conventions
- Top-down plane, **x → right, y → forward**, origin = camera position at t=0, +y = camera's facing direction at t=0.
- Units are **relative room units** (~1 unit ≈ a doorway width). The room fits roughly in [-5, 5] × [0, 10].
- `yaw_deg`: 0 = facing +y. **Positive = camera turned to ITS LEFT** (counterclockwise seen from above).
- `hfov_deg`: one estimate per clip (default 60).
- Times are float seconds `t`, plus `mmss` = `MM:SS.sss` for prompts.
- Every entity has a unique `visual_description`, because Decisions questions refer to things only by words.
- Limits: ≤6 objects, ≤3 actors.

## scene.v1
```json
{
  "schema": "scene.v1",
  "clip": {"id": "tos_014", "duration_s": 9.6, "fps": 24, "width": 1280, "height": 534},
  "room": {"outline": [[-4,0],[4,0],[4,9],[-4,9]], "notes": "door on far-left wall", "confidence": 0.6},
  "objects": [
    {"id": "door_1", "type": "door", "visual_description": "the large wooden double door",
     "shape": "rect", "center": [-4, 6], "size": [0.2, 1.6], "rotation_deg": 0, "color": "#8b5a2b", "confidence": 0.7}
  ],
  "actors": [
    {"id": "a1", "visual_description": "the woman in the grey jacket", "color": "#e74c3c",
     "track": [{"t": 0.0, "mmss": "00:00.000", "x": -1.0, "y": 5.0, "facing_deg": 180, "visible": true}],
     "confidence": 0.7}
  ],
  "camera": {
    "hfov_deg": 60,
    "track": [{"t": 0.0, "mmss": "00:00.000", "x": 0, "y": 0, "yaw_deg": 0}],
    "motion_segments": [{"t0": 0.0, "t1": 4.0, "translation": "dolly_in", "rotation": "pan_left"}],
    "confidence": 0.6
  },
  "low_confidence": [{"entity_id": "door_1", "t0": 2.0, "t1": 4.0, "failed_checks": ["c17"]}],
  "model_notes": "short rationale"
}
```
Enums:
- `translation`: `static | dolly_in | dolly_out | truck_left | truck_right | arc_left | arc_right | handheld_drift`
- `rotation`: `none | pan_left | pan_right`

Rules: keyframes at least every 1 s and at every motion change; IDs stable across rounds; yaw interpolated by shortest arc.

## check.v1 (produced by code in S04, never by an LLM)
```json
{
  "check_id": "c17", "template": "SIDE_LEFT", "t": 2.0, "mmss": "00:02.000",
  "t_end": null,
  "input_ref": {"kind": "frame", "paths": ["frames/tos_014/002000.jpg"]},
  "entities": ["door_1"], "expected": true, "margin": 14.2,
  "question": {"type": "noul",
    "instructions": "Look at this single film frame. Is the large wooden double door in the LEFT half of the image?",
    "criteria": {"true_description": "The door is visible and its center is left of the vertical midline.",
                 "false_description": "The door is not visible, or it is in the right half."}},
  "result": null
}
```
- `input_ref.kind`: `frame` (1 path) | `pair` (2 paths, motion checks)
- `question.type`: `noul` | `choice` (with `options: {key: description}`; use `other` as the catch-all)
- `result` is filled by S05: `{"p_yes": 0.83, "choice_probs": null, "confidence": 0.83, "verdict": "pass|fail|uncertain"}`

Templates: `VISIBLE, NOT_VISIBLE, SIDE_LEFT, SIDE_RIGHT, A_LEFT_OF_B, CLOSER, TOWARD, PAN, DOLLY` (see S04).

## label.v1 (human labels, JSONL, one clip per line)
```json
{"clip_id": "own_007", "source": "own_footage|camerabench|tears_of_steel|sintel|pexels",
 "license": "CC BY 3.0 | consent_form_007 | ...", "split": "dev|test",
 "difficulty": ["easy|handheld|crowd|whip_pan|low_light|long_take"],
 "labeler": "Rishika",
 "entity_map": {"the man in the blue hoodie": "a1"},
 "labels": [
   {"label_id": "L1", "type": "CAM_TRANSLATION", "t0": 0, "t1": 4, "value": "dolly_in"},
   {"label_id": "L2", "type": "CAM_ROTATION", "t0": 4, "t1": 7, "value": "pan_right"},
   {"label_id": "L3", "type": "SIDE", "t": 3.0, "subject": "the man in the blue hoodie", "value": "left"},
   {"label_id": "L4", "type": "A_LEFT_OF_B", "t": 3.0, "a": "...", "b": "...", "value": true},
   {"label_id": "L5", "type": "CLOSER", "t": 6.0, "a": "...", "b": "...", "value": "a"}
 ]}
```
Labels are written BEFORE seeing model output. `entity_map` is filled after the baseline run.

## Run artifacts (out/<clip_id>/)
- `scene_r0.json` (baseline), `scene_r1.json` … `scene_final.json`
- `checks_rN.json` (check.v1 list with results)
- `loop_log.json`: per round: pass_rate, n_checks, n_fail, regressions, tokens, latency_s, accepted, changes
- `map.mp4`, `reel.mp4`

## Change log
- (none yet)
