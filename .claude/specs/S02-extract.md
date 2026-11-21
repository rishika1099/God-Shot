# S02: Extract (Argon → scene.v1; this IS the baseline)

- **Owner:** Akash
- **Status:** todo
- **Depends on:** S00, S01
- **Target:** 1:45
- **Models:** `MODEL_EXTRACT` = gemini-4-argon, static video processing, `fps=cfg.FPS` (4), `thinking_level=MEDIUM`, JSON schema enforced

## Goal
One Argon call turns a clip into a valid `scene.v1`. Round 0 of the loop and the eval BASELINE are the same thing.

## Files
- `godshot/extract.py`
- `godshot/prompts/extract.md`
- `tests/test_extract.py` (offline: uses a cached response fixture)

## Spec
- `extract(clip: Path, fps: int | None = None) -> SceneV1`: upload via S01, call Argon with the video part (`VideoMetadata(fps=...)`) + the prompt, with `response_mime_type="application/json"` and `response_json_schema=SceneV1.model_json_schema()`.
- On validation failure: one repair call ("Fix this JSON to match the schema; change nothing else"), then raise.
- Post-process (pure functions, unit-tested):
  - clamp coordinates to the room bounding box ±1
  - sort keyframes by t
  - add `mmss`
  - insert a keyframe at t=0 and t=duration if missing
  - reject yaw jumps > 90° between keyframes < 1.1 s apart (replace with the midpoint, note it in `model_notes`)
  - de-duplicate `visual_description`s (append a distinguishing detail or drop the duplicate)
- Write `out/<clip_id>/scene_r0.json`. Log tokens and latency to `loop_log.json` (round 0).
- CLI: `python -m godshot baseline clip.mp4 -o out/<id>`.

## Prompt (`prompts/extract.md`), starting text; tune on DEV clips only
```
You are a cinematographer producing an overhead (top-down) blocking diagram of ONE continuous shot.
Watch the whole clip. Output ONLY JSON matching the provided schema.
Coordinate system (mandatory):
- Top-down plane. Origin = camera position at 00:00.000. +y = the direction the camera faces at 00:00.000; +x = to the camera's right.
- Units are relative "room units" (~1 unit ≈ a doorway width). Do NOT claim metric scale.
- yaw_deg: 0 = facing +y; positive = camera turned to ITS LEFT (counterclockwise seen from above).
- Estimate hfov_deg once for the clip (typical 40–75).
Content:
1. room.outline: walls you can infer. Say what you could not see in notes.
2. objects: up to 6 large stable items, each with a short UNIQUE visual_description usable to find it in a single frame.
3. actors: up to 3 people, unique visual_description (clothing/colour), track keyframes at least every 1.0 s and at direction changes; visible=false when off-screen.
4. camera.track: keyframes at least every 1.0 s and at every motion change; motion_segments with translation and rotation from the allowed enums.
Timestamps: t in seconds and mmss as MM:SS.sss.
Be geometrically consistent: anything in the frame's LEFT half at time t must lie to the camera's left given its pose at t.
Give each element a confidence in [0,1]; lower it when guessing (occlusion, darkness, fast motion).
```

## Acceptance criteria
- [ ] Runs on 3 dev clips and produces a valid `scene_r0.json` for each.
- [ ] Sanity check by eye: render it with S07, or for now plot camera x/y in a scratch notebook. The path direction matches the clip on at least 2 of 3.
- [ ] Cached: a second run makes no network call.
- [ ] Median latency per 10 s clip is logged.

## Notes / changelog
-
