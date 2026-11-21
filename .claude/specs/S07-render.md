# S07: Render (animated overhead map)

- **Owner:** Akash
- **Status:** done
- **Depends on:** S00 (build against `mocks/scene_mock.json`); uses `geometry.interp_*` from S03 (write a local stub until it lands)
- **Models:** none (stretch: `MODEL_IMAGE` background)

## Goal
`scene.v1` → `map.mp4`, frame-synced with the clip, in the style of hand-made breakdown reels.

## Files
- `godshot/render.py`
- `godshot/style.py` (colors, sizes)
- `tests/test_render.py`

## Spec
- `render_map(scene, out: Path, fps: float | None = None, size=(1080, 960), ghost: SceneV1 | None = None) -> Path`. Frames = `round(duration * fps)` at the clip's fps, so vstack stays synced.
- Draw each frame (Pillow or matplotlib Agg; pick whichever is faster at 1080×960, target ≥ 60 frames/s rendering):
  - charcoal background `#2b2b2b`, grid every 0.5 units `#3a3a3a`, room outline light grey
  - objects: rotated rounded rects in `color` (fallback palette by type), small label
  - actors: top-down head circle on a shoulder ellipse in `color`, rotated by `facing_deg`, 1.5 s fading trail; hidden when `visible=false`
  - camera: green `#5ad65a` rounded square + translucent FOV wedge (width hfov_deg, length ~4 units) + 2 s fading trail
  - low-confidence entities/time ranges: hatched/dashed + a "?" badge; camera low-confidence → wedge at 40% opacity
  - top-left: current motion segment label ("DOLLY IN · PAN LEFT"); bottom: a thin progress bar
  - `ghost`: draw another scene's camera path faded (for before/after edits)
- Auto-fit: compute the bounds of the room + all tracks, add a 10% margin, keep the aspect ratio.
- Pipe raw RGB frames to ffmpeg via stdin: `-f rawvideo -pix_fmt rgb24 -s WxH -r fps -i - -c:v libx264 -preset veryfast -pix_fmt yuv420p`.
- Stretch: if `MODEL_IMAGE` is set, generate one stylized top-down background from 3 keyframes + a rough layout PNG; draw objects at 30% opacity over it.

## Acceptance criteria
- [x] The mock scene renders a 10 s map.mp4 in < 15 s (0.8 s: 240 frames at ~310 fps incl. x264 encode).
- [x] The camera's FOV wedge visibly points where `yaw_deg` says (yaw +30 = turned left). Pixel-tested at 5 yaws.
- [x] The frame count equals the clip's frame count (for vstack): 240 = 10 s × 24 fps, checked with ffprobe `-count_frames`.
- [x] Screenshot of one frame added to `assets/img/map_mock.png` (mock at t=6 s, mid-pan).

## Notes / changelog
- 2026-11-21 (Akash): `godshot/render.py`, `godshot/style.py`, `tests/test_render.py` (19 tests).
- Uses the real S03 `geometry.interp_camera` / `interp_actor` / `object_points` (S03 was already merged, so no stub was needed).
- Pillow, not matplotlib. The static layer (background, grid, room, objects + labels, ghost path) is drawn once at 2× and downscaled with LANCZOS for antialiasing. The dynamic layer (trails, actors, camera + FOV wedge, low-confidence badges, header, progress bar) is an RGBA overlay composited per frame. About 600 frames/s at 1080×960 on an M-series laptop (spec target ≥ 60); `test_render_speed` asserts ≥ 60.
- Frame i is drawn at t = i / fps, with `round(duration × fps)` frames at the clip's fps (`frame_count()`).
- Extra public helpers for S08/S09: `render_frame(scene, t, size, ghost)` returns one RGB `PIL.Image` (thumbnails, UI previews), plus `motion_label`, `fit_view`, `scene_bounds` and `heading`.
- Conventions: screen up = +y, `heading(deg) = (-sin, cos)`, so +yaw rotates the wedge counterclockwise (camera's left). Actor `facing_deg` uses the same convention. Object `rotation_deg` is CCW, via `geometry.object_points` for bounds.
- Low confidence: `low_confidence.entity_id == "camera"` puts the FOV wedge at 40% opacity with a "?" badge. Objects and actors get a dashed amber outline plus a "?" badge during [t0, t1].
- Object colors: the scene's `color`, unless it is the schema default `#888888`; then a fallback palette by `type` (`style.OBJ_PALETTE`).
- Auto-fit: the bounds of the room, object extents, camera + actor tracks (and the ghost camera path), plus 10% margin on each side, at least 1 unit across, fitted between the 72 px header and 28 px footer, with the aspect ratio kept.
- Requires even `size` (yuv420p); odd sizes raise `ValueError`. ffmpeg missing → `RenderError`.
- Not done: the stretch `MODEL_IMAGE` background (no image model verified yet). Hatch fill for low-confidence entities is a dashed outline only.
