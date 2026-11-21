# S02: Extract (Argon → scene.v1; this IS the baseline)

- **Owner:** Akash
- **Status:** merged (#7); follow-up (motion labels from the track + cut detection) in review. Path-direction criterion still 1/2, prompt untuned
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
- [x] Runs on 3 clips and produces a valid `scene_r0.json` for each (first try, no repair call needed). Local, non-open-licensed smoke-test clips, not the official dev split: see Notes.
- [ ] Sanity check by eye: render it with S07, or for now plot camera x/y in a scratch notebook. The path direction matches the clip on at least 2 of 3. **Not met yet: 1 of 2 usable clips** (see Notes); prompt tuning is the next step.
- [x] Cached: a second run makes no network call (tested offline with `GODSHOT_OFFLINE=1`).
- [x] Median latency per 10 s clip is logged (`latency_s_per_10s` per clip in `loop_log.json`). Live: 27.1 / 49.5 / 23.9 s per 10 s of clip, **median 27.1 s**.

## Notes / changelog
- 2026-11-21 (Akash): built `extract.py`, `prompts/extract.md` (spec starting text, untuned), CLI `baseline`, `tests/test_extract.py` (16 offline tests at first; 36 after the follow-up below, fixture `tests/fixtures/extract_response.json`).
- S01 is merged; `extract._probe/_upload` call `godshot.ingest` directly (a temporary stand-in was used before S01 landed and has been removed). `ingest.upload` has its own 40 h URI cache, and `ingest.probe` rejects clips > 35 s.
- Cache key = `[clip_bytes, MODEL_EXTRACT, prompt, {fps, thinking_level, schema}]`; the upload happens inside the miss path, so a cache hit makes zero network calls. The repair call is cached separately (`kind=extract_repair`).
- Cached payload = `{text, usage, latency_s}`; on a hit, the logged latency is the original call's latency and `cached_calls` counts the hit.
- `clip` metadata always comes from probe, not from the model.
- Deviation: description de-duplication (plus capping to 6 objects/3 actors and de-duplicating ids) runs on the raw dict in `sanitize_raw`, *before* validation, because `SceneV1` rejects duplicate descriptions. `postprocess(scene)` does the rest: it clamps t to [0, duration], sorts keys, drops same-t duplicates, holds the first/last key to t=0 and t=duration (tolerance 10 ms), fixes yaw jumps, clamps coordinates to the room bbox ±1 (no-op when there is no outline), and recomputes every `mmss`.
- Duplicate descriptions get " (the one further left/right/farther away/nearer the camera)" relative to the first entity, or are dropped when they are within 0.25 units of it.
- Yaw jump fix: a spike (out and back) takes the circular midpoint of its neighbours; otherwise the midpoint of prev and current yaw. Each fix is noted in `model_notes` as `[postprocess] ...`. Only camera yaw is fixed; actor `facing_deg` is not.
- `loop_log.json` round 0 also has `latency_s_per_10s`, `calls` and `cached_calls`. `write_loop_log` upserts by `round`, so S06 can append rounds 1+.
- VERIFIED against google-genai 3.6.1: `GenerateContentConfig.response_json_schema`, `VideoMetadata.fps`, usage token fields. Not yet verified live: whether Argon accepts the pydantic schema's `$defs`/`$ref` and `const`.
- 2026-11-21 live run (gemini-4-argon, fps=4, MEDIUM). Three LOCAL clips cut from a commercial film. Personal smoke testing only: not committed (`nocommit_` prefix, `*.mp4` gitignored), not for `eval/` or the demo. The official dev split (S10) still needs to be run.
  | clip | length | result | latency | tokens | path direction by eye (S07 render vs frames) |
  |---|---|---|---|---|---|
  | orbit (single-take 360° around 6 people) | 11.0 s | valid, 4 obj / 3 actors / 13 cam keys | 29.7 s | 9.1k | **rotation correct** (yaw 0 → −220°, the camera turns right as people slide right→left), but the position barely moves (reads as a pan, not an orbit) and the segment label `arc_right · pan_left` contradicts the yaw (`arc_left · pan_right`) |
  | walk (camera follows the team forward) | 6.4 s | valid, 6 / 3 / 8 | 31.6 s | 6.3k | **wrong**: `truck_left → dolly_out`, ends behind/left of the start; the real move is forward |
  | cuts15 (negative test: 15 s with cuts) | 15.0 s | valid, 6 / 3 / 16 | 35.8 s | 9.6k | n/a: about 76° yaw jumps at each cut (just under the 90° repair threshold); should be flagged low-confidence later |
  - Offline re-run (`GODSHOT_OFFLINE=1`) served from cache: `cached 1/1`, no HTTP requests.
  - Argon reported no `thoughts_token_count` at MEDIUM.
  - Tuning ideas (DEV only): ask for the motion enums to be derived from the numeric track (yaw sign ↔ pan direction); spell out "orbit = position moves on a circle AND yaw turns"; add a worked forward-dolly example.
- **Bug fixed during the live run:** `get_client().models.generate_content(...)` on a temporary `Client` failed with "Cannot send a request, as the client has been closed": the Client is garbage-collected mid-call and closes its httpx client. `_generate` now holds `client = get_client()`. **Heads-up for S05/S06/S12:** don't call methods on a temporary `get_client()`.
- **Follow-up (2026-11-21): motion segments from the track + cut detection.**
  - `derive_motion_segments` (in `postprocess`) **replaces** the model's `motion_segments` with labels computed from the camera track: 1 s windows → `geometry.camera_motion` → `classify_motion`, with equal neighbours merged. Rotation: |dyaw| ≥ 8°/s → pan_left/right. Translation: movement ≥ max(0.15 units/s, 5% of the scene distance per second), else static. Mostly sideways while turning back toward the subject → arc_*; otherwise the dominant axis → dolly/truck. `handheld_drift` is never derived. When the derived labels differ from the model's, the model's originals go into `model_notes`. Labels can no longer contradict the map (the orbit said `pan_left` while its yaw turned right).
  - `detect_cuts(clip)`: ffmpeg `scene` score > 0.2 on a 320 px proxy, ignoring the first/last 0.1 s. On the test clips, 0.15–0.2 separated the 5 real cuts from a fast orbit (0 false positives), while 0.3 missed every cut. `mark_cuts` adds a low-confidence `camera` range of ±0.5 s per cut (`failed_checks: ["cut_detected"]`) and a note. Clips are not rejected; S07 shows the dimmed wedge and "?" badge there.
  - Re-run from cache (no API calls): orbit rotation is now `pan_right` throughout (matches the frames). Walk goes from the wrong `truck_left → dolly_out` to `static` (less wrong, still not "forward": a prompt problem). cuts15: 4 cuts flagged (the 5th, at 14.89 s, falls inside the end margin).
  - **Model error found:** on the orbit, Argon's position path curves **counterclockwise** while its yaw turns **clockwise**. These are inconsistent: an orbit faces its centre, so position and yaw turn the same way. The derived translation therefore flickers truck/dolly/arc between windows. That faithfully reports the inconsistent track, so I did not smooth it. Fix it upstream: a prompt rule ("when orbiting, position and yaw rotate the same way"), and S06's verify/correct loop.
