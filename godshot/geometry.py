"""Pure projection math on scene.v1. Positive bearing = point is to the camera's LEFT.

Spec: S03 (.claude/specs/S03*.md) — owner: Siva.

Conventions (contracts.md): top-down plane, x → right, y → forward at t=0.
`yaw_deg` 0 = facing +y, positive = camera turned to ITS LEFT (counterclockwise from above).
A camera pose is a tuple `(x, y, yaw_deg)`; a point is any 2-sequence `(x, y)`.
Only stdlib math is used; no dependencies beyond numpy are allowed (see S03).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from godshot.schema import Actor, Obj, SceneV1

Pose = tuple[float, float, float]
Pt = Sequence[float]


# --------------------------------------------------------------------------- angles


def wrap180(a: float) -> float:
    """Wrap an angle in degrees to [-180, 180)."""
    return (a + 180.0) % 360.0 - 180.0


def _lerp(a: float, b: float, f: float) -> float:
    return a + (b - a) * f


def _lerp_angle(a: float, b: float, f: float) -> float:
    """Shortest-arc interpolation between two angles, result wrapped to [-180, 180)."""
    return wrap180(a + wrap180(b - a) * f)


def _bracket(times: Sequence[float], t: float) -> tuple[int, int, float]:
    """Indices (i, j) of the keyframes around t and the fraction f in [0, 1]. Clamps to the ends.

    `times` must be sorted ascending.
    """
    n = len(times)
    if n == 1 or t <= times[0]:
        return 0, 0, 0.0
    if t >= times[-1]:
        return n - 1, n - 1, 0.0
    for j in range(1, n):
        if times[j] >= t:
            i = j - 1
            span = times[j] - times[i]
            f = 0.0 if span <= 0 else (t - times[i]) / span
            return i, j, f
    return n - 1, n - 1, 0.0  # unreachable for sorted input


# --------------------------------------------------------------------------- interpolation


def interp_camera(scene: SceneV1, t: float) -> Pose:
    """Camera pose (x, y, yaw_deg) at time t: linear x/y, shortest-arc yaw, clamped to the track ends."""
    track = sorted(scene.camera.track, key=lambda k: k.t)
    i, j, f = _bracket([k.t for k in track], t)
    a, b = track[i], track[j]
    return _lerp(a.x, b.x, f), _lerp(a.y, b.y, f), _lerp_angle(a.yaw_deg, b.yaw_deg, f)


def interp_actor(actor: Actor, t: float) -> tuple[float, float, float, bool]:
    """Actor (x, y, facing_deg, visible) at time t.

    Position is linear, facing is shortest-arc. `visible` comes from the nearer keyframe
    (ties go to the earlier one), so visibility flips halfway between keys.
    """
    track = sorted(actor.track, key=lambda k: k.t)
    i, j, f = _bracket([k.t for k in track], t)
    a, b = track[i], track[j]
    visible = a.visible if f <= 0.5 else b.visible
    return _lerp(a.x, b.x, f), _lerp(a.y, b.y, f), _lerp_angle(a.facing_deg, b.facing_deg, f), visible


# --------------------------------------------------------------------------- projection


def bearing_deg(cam: Pose, p: Pt) -> float:
    """Angle of point p relative to the camera's optical axis, in [-180, 180).

    Positive = to the camera's LEFT = appears on the frame's left. |bearing| > 90 means behind.
    """
    cx, cy, yaw = cam
    return wrap180(math.degrees(math.atan2(-(p[0] - cx), p[1] - cy)) - yaw)


def in_view(cam: Pose, p: Pt, hfov: float) -> bool:
    """True if p is inside the horizontal field of view (points behind have |bearing| > 90)."""
    return abs(bearing_deg(cam, p)) < hfov / 2.0


def screen_x(cam: Pose, p: Pt, hfov: float) -> float:
    """Approximate horizontal image position: 0 = left edge, 0.5 = centre, 1 = right edge.

    Linear in bearing (spec S03). Monotone, so left/right order matches a pinhole camera.
    Values outside [0, 1] mean the point is out of frame.
    """
    return 0.5 - bearing_deg(cam, p) / hfov


def distance(cam: Pose, p: Pt) -> float:
    """Euclidean top-down distance from the camera position to p, in room units."""
    return math.hypot(p[0] - cam[0], p[1] - cam[1])


# --------------------------------------------------------------------------- objects


def object_points(obj: Obj) -> list[tuple[float, float]]:
    """Centre + 4 outline points of an object, rotated by `rotation_deg` (counterclockwise from above).

    rect: the 4 corners of a `size = [w, h]` box. circle: the 4 axis extremes at radius `size[0] / 2`.
    """
    cx, cy = obj.center
    if obj.shape == "circle":
        r = obj.size[0] / 2.0
        offsets = [(r, 0.0), (0.0, r), (-r, 0.0), (0.0, -r)]
    else:
        hw, hh = obj.size[0] / 2.0, obj.size[1] / 2.0
        offsets = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    th = math.radians(obj.rotation_deg)
    c, s = math.cos(th), math.sin(th)
    pts = [(cx, cy)]
    pts += [(cx + dx * c - dy * s, cy + dx * s + dy * c) for dx, dy in offsets]
    return pts


def object_bearing_range(cam: Pose, obj: Obj) -> tuple[float, float]:
    """(min, max) bearing over the object's points.

    Bearings are unwrapped around the centre's bearing, so an object straddling the ±180°
    seam behind the camera gives a contiguous range (values may leave [-180, 180)).
    """
    pts = object_points(obj)
    b_c = bearing_deg(cam, pts[0])
    bs = [b_c + wrap180(bearing_deg(cam, p) - b_c) for p in pts]
    return min(bs), max(bs)


# --------------------------------------------------------------------------- camera motion


def _scene_points(scene: SceneV1, t: float) -> list[tuple[float, float]]:
    """Object centres + actor positions at t; falls back to the room outline if the scene is empty."""
    pts: list[tuple[float, float]] = [(o.center[0], o.center[1]) for o in scene.objects]
    for a in scene.actors:
        x, y, _, _ = interp_actor(a, t)
        pts.append((x, y))
    if not pts:
        pts = [(p[0], p[1]) for p in scene.room.outline]
    return pts


def _total_dyaw(scene: SceneV1, t0: float, t1: float) -> float:
    """Signed yaw change over [t0, t1], summing shortest arcs between keyframes (handles > 180° pans)."""
    inner = sorted(k.t for k in scene.camera.track if t0 < k.t < t1)
    times = [t0, *inner, t1]
    yaws = [interp_camera(scene, t)[2] for t in times]
    return sum(wrap180(b - a) for a, b in pairwise(yaws))


def camera_motion(scene: SceneV1, t0: float, t1: float) -> dict[str, float]:
    """Camera movement from t0 to t1, expressed in the camera's frame at t0.

    - `forward`: displacement along the t0 viewing direction (+ = moved toward the scene / dolly in)
    - `lateral`: displacement to the t0 camera's RIGHT (+ = truck right, - = truck left)
    - `dyaw`: signed rotation in degrees (+ = turned left / pan left)
    - `mean_scene_dist`: mean distance from the t0 camera to object centres + actor positions
      (room outline if there are none; 0.0 if the scene is empty)
    """
    x0, y0, yaw0 = interp_camera(scene, t0)
    x1, y1, _ = interp_camera(scene, t1)
    dx, dy = x1 - x0, y1 - y0
    th = math.radians(yaw0)
    fwd = (-math.sin(th), math.cos(th))  # yaw 0 → +y; positive yaw turns toward -x
    right = (math.cos(th), math.sin(th))
    pts = _scene_points(scene, t0)
    cam0 = (x0, y0, yaw0)
    mean_d = sum(distance(cam0, p) for p in pts) / len(pts) if pts else 0.0
    return {
        "forward": dx * fwd[0] + dy * fwd[1],
        "lateral": dx * right[0] + dy * right[1],
        "dyaw": _total_dyaw(scene, t0, t1),
        "mean_scene_dist": mean_d,
    }
