# S02: Extract (Argon → scene.v1; this IS the baseline)

- **Owner:** Akash
- **Status:** in progress (code + offline tests done; live run on 3 dev clips pending `.env` + clips)
- **Depends on:** S00, S01
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
- [x] Cached: a second run makes no network call (tested offline with `GODSHOT_OFFLINE=1`).
- [x] Median latency per 10 s clip is logged (`latency_s_per_10s` per clip in `loop_log.json`; median is taken across clips by S11).

## Notes / changelog
- 2026-11-21 (Akash): built `extract.py`, `prompts/extract.md` (spec starting text, untuned), CLI `baseline`, `tests/test_extract.py` (20 offline tests, fixture `tests/fixtures/extract_response.json`).
- S01 is merged; `extract._probe/_upload` call `godshot.ingest` directly (a temporary stand-in was used before S01 landed and has been removed). `ingest.upload` has its own 40 h URI cache, and `ingest.probe` rejects clips > 35 s.
- Cache key = `[clip_bytes, MODEL_EXTRACT, prompt, {fps, thinking_level, schema}]`; the upload happens inside the miss path, so a cache hit makes zero network calls. The repair call is cached separately (`kind=extract_repair`).
- Cached payload = `{text, usage, latency_s}`; on a hit, the logged latency is the original call's latency and `cached_calls` counts the hit.
- `clip` metadata always comes from probe, not from the model.
- Deviation: description de-duplication (plus capping to 6 objects/3 actors and de-duplicating ids) runs on the raw dict in `sanitize_raw`, *before* validation, because `SceneV1` rejects duplicate descriptions. `postprocess(scene)` does the rest: it clamps t to [0, duration], sorts keys, drops same-t duplicates, holds the first/last key to t=0 and t=duration (tolerance 10 ms), fixes yaw jumps, clamps coordinates to the room bbox ±1 (no-op when there is no outline), and recomputes every `mmss`.
- Duplicate descriptions get " (the one further left/right/farther away/nearer the camera)" relative to the first entity, or are dropped when they are within 0.25 units of it.
- Yaw jump fix: a spike (out and back) takes the circular midpoint of its neighbours; otherwise the midpoint of prev and current yaw. Each fix is noted in `model_notes` as `[postprocess] ...`. Only camera yaw is fixed; actor `facing_deg` is not.
- `loop_log.json` round 0 also has `latency_s_per_10s`, `calls` and `cached_calls`. `write_loop_log` upserts by `round`, so S06 can append rounds 1+.
- VERIFIED against google-genai 3.6.1: `GenerateContentConfig.response_json_schema`, `VideoMetadata.fps`, usage token fields. Not yet verified live: whether Argon accepts the pydantic schema's `$defs`/`$ref` and `const`.
- TODO (live): run `python -m godshot baseline clips/<dev>.mp4 -o out/<id>` on 3 dev clips, check path direction by eye.
