# S09: Web UI (upload, view, edit map, cached demo mode)

- **Owner:** Siva
- **Status:** in progress (UI, editor, API + tests done; reels need S07/S08, live runs need S06, cached demo needs real runs)
- **Depends on:** S08 (S06 `pipeline.run` for live runs)
- **Models:** none directly (calls the pipeline); the text-edit command uses `MODEL_CHEAP`

## Goal
A local web page to run the pipeline, watch the verify loop, view the reel, edit the map, and replay cached runs offline. **No Streamlit.**

## Files
- `godshot/web/app.py` (FastAPI)
- `godshot/web/static/index.html`, `app.js`, `style.css` (vanilla JS + canvas, no build step)
- `godshot/edit.py` (`apply_edit(scene, edit) -> SceneV1`, `text_edit(scene, instruction) -> SceneV1`)

## Spec
- Routes:
  - `POST /api/run` (upload clip) → job id; runs `pipeline.run` in the background
  - `GET /api/job/{id}` → stage + loop_log so far (poll every 1 s)
  - `GET /api/runs` → list of `out/*` folders (cached demo runs)
  - `GET /api/run/{clip_id}` → scene_final, checks per round, loop_log, reel URL
  - `POST /api/run/{clip_id}/edit` (edited scene JSON) → re-render map + reel → URL
  - `POST /api/run/{clip_id}/text-edit` ({instruction}) → edited scene (Hydrogen) → re-render
  - static `/out/...` for videos
- Page layout: left = reel video player; right = an "Inspector" with round tabs (pass rate per round, list of checks with ✔/✘/? and the frame thumbnail on hover), and "low confidence" items.
- Map editor (canvas): draws the scene top-down; drag camera keyframe dots, a rotate handle for yaw, drag actor keyframes and objects; an FOV slider; a "move preset" dropdown per segment (static, dolly in/out, truck, pan, arc around actor); buttons "Re-render" and "Re-shoot" (S12).
- `GODSHOT_OFFLINE=1` → read-only cached demo mode; must work with Wi-Fi off.
- CLI: `python -m godshot web` → uvicorn on :8000.

## Acceptance criteria
- [ ] Cached demo mode replays 2 pre-computed runs with Wi-Fi off.
- [ ] Dragging the camera and pressing Re-render produces a new reel in < 30 s, with the original path shown as a ghost.
- [x] The text edit "make it a slow arc to the right around the actor" changes the camera track sensibly (tested with a fake Hydrogen reply; live Hydrogen call still to try).

## Notes / changelog
- 2026-11-21 (Siva): `godshot/web/app.py`, `static/index.html|app.js|style.css`, `godshot/edit.py`,
  `python -m godshot web [--mock] [--port]` in `__main__.py` (only the S09 stub was filled in).
  Tests: `tests/test_edit.py` (17), `tests/test_web.py` (9). Live check: server starts, all routes answer.
  - **Works before S06/S07/S08 land.** The browser draws the top-down map itself (room, objects, actors with
    trails, camera path + FOV wedge, low-confidence "?" + dimming, motion-segment label) synced to the video's
    `currentTime`, or to its own clock when there's no video. Re-render calls `render.render_map` +
    `compose.compose_reel` when they exist; until then the edit is saved and the API answers `render: "pending"`.
  - **Routes** (as specced, plus two): `GET /api/runs`, `GET /api/run/{id}`, `POST /api/run` (upload),
    `GET /api/job/{id}`, `POST /api/run/{id}/edit`, `POST /api/run/{id}/text-edit`, plus
    `POST /api/run/{id}/apply` (preview one edit op without saving; used by the preset button) and
    `POST /api/run/{id}/reshoot` (S12 hook: background job calling `reshoot.reshoot` + `reshoot.conformance`),
    `GET /frame?path=` (check thumbnails, confined to frames/, out/, eval/runs/), `GET /api/status`.
  - **Upload** sends the clip as the raw request body (`?name=` query), so no `python-multipart` dependency.
    Saved to `clips/uploads/<stem>_<6 hex>.mp4`, copied to `out/<id>/clip.mp4`, then `pipeline.run` in a thread.
    Job stage is inferred from the files S06 writes (scene_r0 → checks_rN → scene_final → reel).
  - **Runs listed** from `out/*` and `eval/runs/full/*` (a folder needs scene_final.json or scene_r0.json).
    Clip for the player: `out/<id>/clip.mp4`, else `clips/`, `eval/clips/`, `clips/uploads/` by id.
  - **Edits** are saved as `scene_edit_N.json` (+ `map_edit_N.mp4` / `reel_edit_N.mp4` when rendered), never
    overwriting the verified `scene_final.json`; the original camera path is drawn as a dashed ghost.
  - **edit.py**: `apply_edit` ops = move_camera_key, set_yaw, set_fov (clamped 10–120), move_object,
    move_actor_key, preset, scene. Presets rebuild the camera in [t0, t1] with a key every 0.5 s and carry the
    rest of the shot rigidly from the new end pose; motion_segments are split/replaced. Arc orbits the target
    (default: first actor) at constant radius keeping it centred; arc_right = moves to its right + turns left
    (same convention as S10/S11). Defaults: 1.5 units dolly/truck, 30° pan, 35° arc.
  - **text_edit**: Hydrogen (`MODEL_CHEAP`, thinking LOW, JSON schema) returns a small preset plan
    `{presets: [{move, t0, t1, amount, target}], hfov_deg, note}`, which `apply_preset` executes. More reliable
    than asking it to rewrite the whole scene. Cached (kind `text_edit`); a bad step is skipped with a warning.
    VERIFY on first live call: `thinking_level="LOW"` on gemini-4-hydrogen.
  - **Offline** (`GODSHOT_OFFLINE=1`): read-only; upload/edit/text-edit/reshoot → 403; preview + viewing work.
  - **`--mock`** seeds `out/mock_001` from `mocks/` with a `MOCK` marker; the UI shows a red "MOCK DATA" badge.
  - Gotcha (Windows): non-ASCII in CLI output crashes on the cp1252 console; the web command prints ASCII only.
    `godshot smoke` (S00) still prints "→": run it with `PYTHONIOENCODING=utf-8` on Windows.
  - 2026-11-21 integration with merged S07 (#6): every move preset's output, with the original as `ghost`, renders
    through `render.render_frame` at 1080×960. S07 runs at ~92 frames/s on this laptop with the prepared
    background (a 10 s clip = 240 frames ≈ 2.6 s before ffmpeg encoding), so the < 30 s re-render target is
    realistic. `compose_reel(clip, map_mp4, out)` on `akash/s08-compose` matches the call here. Without ffmpeg,
    re-render saves the edit and answers `render: "error"` with S07's "ffmpeg not found" note; with no clip it
    answers `map_only` and the page plays the map video. Tests use fake render/compose (no ffmpeg needed).
  - Still open: acceptance 1 (cached demo of 2 real runs with Wi-Fi off) needs 2 real S06 runs; acceptance 2
    (new reel < 30 s) needs S07/S08. Not yet checked in a real browser by a human.
