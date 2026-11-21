# S03: Geometry (projection math)

- **Owner:** Siva
- **Status:** done
- **Depends on:** S00
- **Target:** 1:15
- **Models:** none (pure numpy)

## Goal
Deterministic math to predict what the camera sees from a `scene.v1` at any time t. Used by checks (S04), scoring (S11) and the renderer (S07: FOV cone).

## Files
- `godshot/geometry.py`
- `tests/test_geometry.py`

## Spec
- `interp_camera(scene, t) -> (x, y, yaw_deg)`: linear x/y, shortest-arc yaw between bracketing keyframes; clamp to the ends.
- `interp_actor(actor, t) -> (x, y, facing_deg, visible)`.
- `bearing_deg(cam, p) -> float`: `wrap180(degrees(atan2(-(px-cx), (py-cy))) - yaw)`. **Positive = point is to the camera's LEFT = appears on the frame's left.**
- `in_view(cam, p, hfov) -> bool`: `|bearing| < hfov/2` and the point is in front (the bearing formula already covers behind as |b| > 90).
- `screen_x(cam, p, hfov) -> float` ≈ `0.5 - bearing/hfov` (0 = left edge, 1 = right edge).
- `distance(cam, p)`, `object_points(obj)` (center + 4 corners, rotated), `object_bearing_range(cam, obj)`.
- `camera_motion(scene, t0, t1) -> {"forward": d, "lateral": d, "dyaw": deg, "mean_scene_dist": d}` in the camera's t0 frame.
- `wrap180(a)` helper.

## Acceptance criteria
- [x] Unit tests cover: a point directly ahead → bearing 0, screen_x 0.5; a point to the camera's left at 45° → bearing +45, screen_x < 0.5; yaw +90 (turned left) makes a point at (−5, 0) appear ahead; behind → not in view; shortest-arc interpolation 350°→10° passes through 0°.
- [x] The mock scene's door is predicted on the left at t=2 if the mock says so (cross-check with `mocks/checks_mock.json`).
- [x] No dependencies beyond numpy.

## Notes / changelog
- 2026-11-21 (Siva): implemented `godshot/geometry.py` + `tests/test_geometry.py` (33 tests).
  - API: `wrap180`, `interp_camera`, `interp_actor`, `bearing_deg`, `in_view`, `screen_x`, `distance`,
    `object_points`, `object_bearing_range`, `camera_motion`. Pose = `(x, y, yaw_deg)` tuple; points are any 2-sequence.
  - Uses stdlib `math` only (no numpy needed for scalar math).
  - Conventions pinned down where the spec was silent:
    - `wrap180` returns [-180, 180); `interp_camera` returns a wrapped yaw.
    - `interp_actor.visible` comes from the NEARER keyframe (tie → earlier), so visibility flips halfway between keys.
    - `camera_motion.lateral` is + to the t0 camera's RIGHT (truck_right > 0). `dyaw` sums shortest arcs between the
      keyframes inside [t0, t1], so pans > 180° are reported correctly (+ = pan left).
    - `camera_motion.mean_scene_dist` = mean distance from the t0 camera to object centres + actor positions at t0;
      room outline if the scene has none; 0.0 if empty.
    - `object_points`: rect → centre + 4 corners of `size=[w, h]` rotated CCW by `rotation_deg`; circle → centre + 4 axis
      extremes at radius `size[0]/2`. `object_bearing_range` unwraps around the centre bearing (contiguous across ±180°).
    - `screen_x` is linear in bearing as specced (same left/right order as a pinhole camera).
  - Mock cross-check: every item in `mocks/checks_mock.json` reproduces its `expected` AND `margin` from geometry alone.
    In the mock, the door is on the camera's LEFT at t=2 but outside the 60° hfov (bearing 38.7°); it enters view after
    the pan (t=8). S04 can rely on margins = |bearing| − hfov/2 (VISIBLE), |bearing| (SIDE), bearing gap (A_LEFT_OF_B),
    distance ratio (CLOSER), % distance change (TOWARD), |dyaw| (PAN), |forward| (DOLLY), as used by the mocks.
