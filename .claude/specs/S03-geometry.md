# S03: Geometry (projection math)

- **Owner:** Siva
- **Status:** todo
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
- [ ] Unit tests cover: a point directly ahead → bearing 0, screen_x 0.5; a point to the camera's left at 45° → bearing +45, screen_x < 0.5; yaw +90 (turned left) makes a point at (−5, 0) appear ahead; behind → not in view; shortest-arc interpolation 350°→10° passes through 0°.
- [ ] The mock scene's door is predicted on the left at t=2 if the mock says so (cross-check with `mocks/checks_mock.json`).
- [ ] No dependencies beyond numpy.

## Notes / changelog
-
