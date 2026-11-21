# S09: Web UI (upload, view, edit map, cached demo mode)

- **Owner:** Siva
- **Status:** todo
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
- [ ] The text edit "make it a slow arc to the right around the actor" changes the camera track sensibly.

## Notes / changelog
-
