# S07: Render (animated overhead map)

- **Owner:** Akash
- **Status:** todo
- **Depends on:** S00 (build against `mocks/scene_mock.json`); uses `geometry.interp_*` from S03 (write a local stub until it lands)
- **Target:** 2:00
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
- [ ] The mock scene renders a 10 s map.mp4 in < 15 s.
- [ ] The camera's FOV wedge visibly points where `yaw_deg` says (yaw +30 = turned left).
- [ ] The frame count equals the clip's frame count (for vstack).
- [ ] Screenshot of one frame added to `assets/img/map_mock.png`.

## Notes / changelog
-
