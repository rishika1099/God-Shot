# S12: Re-shoot with Omni + conformance check

- **Owner:** Prajesh (incl. the Omni spike and the re-shoot button in the S09 UI)
- **Status:** in progress (all S12 code built + tested, live end-to-end 30/30; S09 already calls it. Left: UI smoke test, human check of 5 clips, demo pairs on real S02/S06 maps)
- **Depends on:** S06, S09 (the spike has no dependencies)
- **Models:** `MODEL_RESHOOT` = gemini-omni-1.2-flash (Interactions API); `MODEL_CHEAP` = gemini-4-hydrogen to write the shot description; `MODEL_VERIFY` for conformance

## Goal
From an EDITED map, generate a new shot with Omni, then verify whether the generated video actually follows the edit.

## Files
- `godshot/reshoot.py`
- `godshot/prompts/shot_description.md`
- `godshot/__main__.py` (`reshoot`, `conformance` commands)
- `scripts/omni_spike.py`, `scripts/blockout_spike.py`
- `tests/test_reshoot.py`

## Spec
- **Spike (Prajesh, do first; no dependencies, keep it small):** `scripts/omni_spike.py` takes 1 reference frame + "same room, the camera slowly arcs to the right around the person" → 5 s 360p. Record latency and whether it roughly follows; write the result in Notes. Go/no-go.
- `describe_shot(scene_edited) -> str` (Hydrogen): turn the camera track + motion segments + actor/object `visual_description`s into a concrete shot description (start framing, move, end framing, which objects are in which third, lens feel from hfov).
- `reshoot(clip, scene_orig, scene_edited, out, resolution="360p", duration="8s") -> Path`: ≤ 6 reference images (3 keyframes from the original + up to 3 actor/object crops via bounding box guesses or full frames); `response_format={"type":"video","duration":...,"resolution":...,"aspect_ratio":"16:9"}`; save mp4; cache by (refs + prompt + params). Label the output "AI-generated re-shoot".
- `conformance(generated_clip, scene_edited) -> {"score": k/n, "checks": [...]}`: run S04 `make_checks(scene_edited)` (times rescaled to the generated duration) + S05 `verify` on the generated frames. Also extract a scene from the generated clip (S02) and compare motion segments with the edit.
- Run generations in parallel in the background (Tier 3 limits); never generate live on stage. Pre-render 2–3 demo pairs before the demo. Use `compose_compare` (S08).

## Acceptance criteria
- [x] Spike result is logged (latency, instruction-following yes/no).
- [ ] One edited map → generated clip → conformance score shown in the UI.
- [ ] A human checks 5 generated clips against their edits; agreement with the verifier is reported in results.md.

## Notes / changelog
- **Spike (2026-11-21): GO.** `gemini-omni-1.2-flash`, 1 reference frame, prompt "camera slowly arcs to the right around the person", 5 s at 360p. Output is 640×360 at 24 fps, 5.03 s, with audio. **Latency ~24–30 s** per clip; a cached rerun takes 0 s.

  | Clip (Tears of Steel, CC BY 3.0) | Shot | Follows the arc? | Same room/person? |
  |---|---|---|---|
  | `tos_spike_01` (film 160.0–172.2 s) | wide, 1 person from behind, dark interior | **yes**, a clear arc to his profile (also pushes in) | yes; clothing drift (shirt → jacket) |
  | `tos_spike_02_bridge` (25.1–40.1 s) | wide exterior, 2 people on a bridge | **partly**, a gentle arc | yes |
  | `tos_spike_03_room` (238.8–253.3 s) | tight interior, 3 people close together | **no**, nearly static | yes |

  Takeaways: Omni follows camera moves on **wide shots with room to move**, and weakly on tight, crowded framings. Identity and colour hold well, with small clothing drift. Output is always 16:9, so 2.39:1 sources get reframed tighter. This is exactly why `conformance()` matters: it should catch weak results like spike 03.
- **Refused input:** a frame from a commercial film (personal local test, never committed) failed with a generic `INVALID_ARGUMENT`. The same request with a neutral image succeeded, so it was a content filter, not the request format. Use open-licensed or own footage only.
- **Built:** `reshoot.generate_video()` (background interaction, polls every 5 s, 300 s timeout, inline video, cached under `.cache/omni/`, error codes surfaced). `scripts/omni_spike.py` writes `out/spike/<clip>/{ref.jpg, omni.mp4, report.json}`. Field names verified against google-genai 3.6.1: `response_format={"type":"video","delivery":"inline","duration":"5s","resolution":"360p","aspect_ratio":"16:9"}`, statuses `completed|failed|…`, video found in `steps[type=model_output].content[type=video].data`.
- **Map-exact method (2026-11-21), now the primary approach:** `render_blockout(scene_edited)` renders a grey-box previs along the EXACT edited camera track (x, y, yaw, hfov; same pinhole convention as `geometry.py`; objects as boxes from `center/size/rotation`, actors as capsules from their tracks). It's sent with a real reference frame to Omni `video_config.task="edit"`. Projection is tested to ±4 px of the geometry. Text-only prompting is the fallback.
  - API facts (verified by 400s): with `task="edit"`, `duration` and `aspect_ratio` must NOT be set in `response_format`, because they come from the guide video. Input order is guide video, then refs, then text.
  - **Blockout spike** (45° orbit right around the man, `scripts/blockout_spike.py`, ~31 s per call). Conformance vs the edited map (S04 `make_checks` → S05 `verify` → `predict == observed`):

    | Video | Score |
    |---|---|
    | Omni edit + blockout guide | **0.97** (29/30 usable) |
    | Omni words-only | 0.88 (29/33) |
    | blockout itself | 0.69 (the verifier is unsure about grey boxes, so it's not a ceiling) |

    The guided edit kept the man's plaid shirt (words-only turned it into a jacket) and held the orbit radius. One clip, so the result is suggestive, not significant.
  - **Gotcha for S04 (Rishika):** PAN questions on ARC segments are ambiguous (an orbit is rotation + translation); both videos missed most PAN checks. Consider skipping PAN pair checks inside arc segments.
  - `conformance()` is implemented: frames are extracted at times rescaled to the generated clip's duration, `uncertain` answers are not usable, and an abstention counts as a miss. It writes `conformance.json`.
  - Fixes in my own earlier code: `config.get_client()` now returns one shared client (inline `get_client().models.list()` used to close it); `ingest.probe()` now prefers the video-stream duration (Omni mp4s have audio 35 ms longer than the video).
- **Proposed S09 hook (agree with Siva):** `POST /api/run/{clip_id}/reshoot` (body: edited scene JSON) → background job → `reshoot.reshoot(clip, scene_orig, scene_edited, out)` → `{video_url, conformance: {score, hits, usable, checks}}`. Generation takes ~30 s, so poll it like `/api/job/{id}`.
- **Full S12 built (2026-11-21):**
  - `shot_facts` / `describe_shot(scene, duration_s, polish=False)`: a **deterministic** template from S03 geometry (lens from hfov; per-segment moves in metres and degrees; arcs name the subject; entities entering and leaving with the edge; start/end framing in thirds with distances; actor walk directions; a continuity rule). Same map, same prompt, and ids never leak. `polish=True` adds a cached Hydrogen rewrite under strict rules (`prompts/shot_description.md`). **Deviation:** the spec had Hydrogen write the description; it's now optional polish, because a deterministic template is testable and can't drop facts.
  - `edit_prompt`: a legend mapping each blockout colour to its entity ("the red figure is the grey-haired man…") plus the description. `blockout_palette` gives every entity a distinct colour name (grey and black are swapped out, since they vanish against the grey room).
  - `reference_images`: 3 keyframes (10/50/90%) from the original clip plus up to 3 entity crops (actors first; a vertical strip around the geometry's screen_x). Capped at 6.
  - `reshoot(clip, scene_orig, scene_edited, out, resolution, duration, mode="blockout"|"text", polish, take)`: the guide is cut to `duration`. Writes the labelled mp4 at `out`, plus `<stem>_work/` (refs, blockout.mp4, raw.mp4, reshoot.json). `burn_label` draws an "AI-generated re-shoot" badge with Pillow and overlays it with ffmpeg (no drawtext in this build).
  - `reshoot_best(..., n)`: takes run in parallel, each is scored by `conformance` on the UNLABELLED raw video, and the best is kept → `reshoot_best.mp4/.json`.
  - Demo pairs use S08 `compose.compose_compare(original, reshoot, map_mp4, out)` (Akash).
  - CLI: `python -m godshot reshoot CLIP --orig A.json --edited B.json -o DIR --n 2`, and `python -m godshot conformance VIDEO --edited B.json`.
  - S09 hook: Siva's S09 already has the route (`POST /api/run/{clip_id}/reshoot` → `reshoot.reshoot(...)` → `reshoot.conformance(out, edited)`) and the button; my signatures match his calls. `conformance()` scores the unlabelled `raw.mp4` when given a labelled output, so the badge never confuses the verifier. (My own router draft was dropped as a duplicate.)
  - Tests: `tests/test_reshoot.py`, 20 tests, offline (fake Omni/verifier, real tiny ffmpeg videos).
- **Live end-to-end (2026-11-21):** `godshot reshoot clips/tos_spike_01.mp4` with hand-made maps (original static, edited 45° orbit right), standing in until S02 maps exist. 2 takes in parallel took **40 s total**; take 1 scored **1.00 (30/30)** and take 2 0.97 (28/29). Outputs are in `out/demo/tos_spike_01_arc/` (`reshoot_best.mp4`, `demo_pair.mp4`).
- **Not done:** a live check through Siva's UI (should work as-is; try `python -m godshot web` on a run); 5 clips human-checked vs the verifier → results.md (human task); demo pairs on REAL extracted maps (needs S02/S06). The spec's "extract a scene from the generated clip (S02) and compare motion segments" waits on S02.
- **Fix (2026-11-21): full length + keep the performance.** Rishika's review found two problems: (1) `reshoot()` defaulted to `duration="8s"` and cut the guide video, so a 9 s clip gave an 8 s re-shoot; (2) the prompt only said actors "stay in place" (plus "no zoom") and never described the acting, so Omni froze or invented the performance.
  - Length: the default is now the ORIGINAL clip's length. Verified live that Omni `edit` returns guide-length output (12 s guide → 12.0 s; 14.5 s guide → 14.5 s). Above `OMNI_EDIT_MAX_S` (15 s per the docs; VERIFY past 12 s) the shot is **time-scaled to fit, not cut** (`time_scale` in reshoot.json, every prompt time rescaled). Text mode is clamped to Omni's 5–8 s range the same way. The CLI `--duration` is now an optional cap.
  - Performance: `performance_beats(clip, scene_orig)` makes one cached Hydrogen video call (JSON schema enforced) that returns timestamped beats ("who does what, when"). The edit prompt now says the grey boxes give only the camera and positions, the people perform exactly as in the original (beats listed), and **change ONLY the camera position, angle, point of view and field of view**. "No zoom" is gone; FOV edits are described ("a 45° field of view instead of the original 50°, tighter framing, like zooming in"). "Stays in place" became "keeps the same spot on the floor". If the call fails, it falls back to "behave as in the references" with a warning.
  - Live check (`tos_spike_03_room`, 3 people talking, real S02 map, edit = 30° orbit right + FOV 50→45): a 14.5 s output, 6 accurate beats (arm round the shoulder, gesturing, leaning in, the shoulder pat at 10.5–13.5 s, stepping away), and the **same acting at the same moments** in the original vs the re-shoot. Conformance was 0.89 (50/56). Omni took 77 s for 14.5 s (vs ~37 s for 5 s), so the full length costs time; cap with `--duration` if needed.
  - Tests: 7 new, and all S12 tests are now strictly offline (an autouse stub; `real_beats` marker for the fake-client tests). A first draft accidentally uploaded a synthetic test clip; fixed before commit.
- **Next:** A/B against `gemini-omni-flash-preview` on the same frames (score with conformance); prompts should name the move concretely and prefer wide reference frames. `describe_shot` / `reshoot` / `conformance` after S06/S09. The conformance step must use the shared bank (S04 `predict` vs S05 `observed`), not old-style verdicts.
