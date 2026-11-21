"""S03 geometry tests: projection conventions, interpolation, and a cross-check against the S00 mocks."""

from __future__ import annotations

import math
import random

import pytest

from godshot import geometry as g
from godshot.schema import CheckV1, Obj, SceneV1


def _scene(
    cam_track: list[dict], objects: list[dict] | None = None, actors: list[dict] | None = None
) -> SceneV1:
    return SceneV1.model_validate(
        {
            "clip": {"id": "t", "duration_s": 10.0, "fps": 24, "width": 1280, "height": 720},
            "objects": objects or [],
            "actors": actors or [],
            "camera": {"hfov_deg": 60, "track": cam_track},
        }
    )


def _obj(**kw) -> Obj:
    base = {"id": "o", "type": "box", "visual_description": "a box", "center": [0, 5], "size": [2, 1]}
    return Obj.model_validate({**base, **kw})


# --------------------------------------------------------------------------- wrap180


@pytest.mark.parametrize(
    ("a", "want"), [(0, 0), (180, -180), (-180, -180), (190, -170), (-190, 170), (360, 0), (720 + 45, 45)]
)
def test_wrap180(a: float, want: float) -> None:
    assert g.wrap180(a) == pytest.approx(want)


def test_wrap180_range_property() -> None:
    rng = random.Random(0)
    for _ in range(1000):
        a = rng.uniform(-2000, 2000)
        w = g.wrap180(a)
        assert -180 <= w < 180
        assert math.isclose(math.cos(math.radians(a)), math.cos(math.radians(w)), abs_tol=1e-9)


# --------------------------------------------------------------------------- spec acceptance cases

ORIGIN = (0.0, 0.0, 0.0)


def test_point_directly_ahead() -> None:
    assert g.bearing_deg(ORIGIN, (0, 5)) == pytest.approx(0)
    assert g.screen_x(ORIGIN, (0, 5), 60) == pytest.approx(0.5)
    assert g.in_view(ORIGIN, (0, 5), 60)


def test_point_left_45() -> None:
    p = (-5, 5)
    assert g.bearing_deg(ORIGIN, p) == pytest.approx(45)
    assert g.screen_x(ORIGIN, p, 100) < 0.5
    assert g.in_view(ORIGIN, p, 100)
    assert not g.in_view(ORIGIN, p, 60)


def test_point_right_is_negative_bearing() -> None:
    assert g.bearing_deg(ORIGIN, (5, 5)) == pytest.approx(-45)
    assert g.screen_x(ORIGIN, (5, 5), 100) > 0.5


def test_yaw_left_brings_left_point_ahead() -> None:
    cam = (0.0, 0.0, 90.0)  # turned to its left → facing -x
    assert g.bearing_deg(cam, (-5, 0)) == pytest.approx(0)
    assert g.in_view(cam, (-5, 0), 60)
    # what was ahead (+y) is now to the camera's RIGHT
    assert g.bearing_deg(cam, (0, 5)) == pytest.approx(-90)


@pytest.mark.parametrize("p", [(0, -5), (1, -5), (-1, -5), (3, -1)])
def test_behind_not_in_view(p: tuple[float, float]) -> None:
    assert abs(g.bearing_deg(ORIGIN, p)) > 90
    assert not g.in_view(ORIGIN, p, 179)


def test_shortest_arc_350_to_10_passes_through_0() -> None:
    sc = _scene([{"t": 0, "x": 0, "y": 0, "yaw_deg": 350}, {"t": 2, "x": 0, "y": 0, "yaw_deg": 10}])
    assert g.interp_camera(sc, 1.0)[2] == pytest.approx(0, abs=1e-9)
    assert g.wrap180(g.interp_camera(sc, 0.5)[2]) == pytest.approx(-5)
    assert g.interp_camera(sc, 1.5)[2] == pytest.approx(5)


def test_shortest_arc_10_to_350_goes_backwards() -> None:
    sc = _scene([{"t": 0, "x": 0, "y": 0, "yaw_deg": 10}, {"t": 1, "x": 0, "y": 0, "yaw_deg": 350}])
    assert g.interp_camera(sc, 0.5)[2] == pytest.approx(0, abs=1e-9)


# --------------------------------------------------------------------------- interpolation


def test_interp_camera_linear_and_clamped() -> None:
    sc = _scene([{"t": 1, "x": 0, "y": 0, "yaw_deg": 0}, {"t": 3, "x": 2, "y": 4, "yaw_deg": 20}])
    assert g.interp_camera(sc, 2) == pytest.approx((1, 2, 10))
    assert g.interp_camera(sc, -5) == pytest.approx((0, 0, 0))
    assert g.interp_camera(sc, 99) == pytest.approx((2, 4, 20))


def test_interp_camera_unsorted_and_single_key() -> None:
    sc = _scene([{"t": 3, "x": 2, "y": 4, "yaw_deg": 20}, {"t": 1, "x": 0, "y": 0, "yaw_deg": 0}])
    assert g.interp_camera(sc, 2) == pytest.approx((1, 2, 10))
    one = _scene([{"t": 0, "x": 1, "y": 2, "yaw_deg": 30}])
    assert g.interp_camera(one, 5) == pytest.approx((1, 2, 30))


def test_interp_actor_position_facing_visibility() -> None:
    sc = _scene(
        [{"t": 0, "x": 0, "y": 0}],
        actors=[
            {
                "id": "a1",
                "visual_description": "a person",
                "track": [
                    {"t": 0, "x": 0, "y": 5, "facing_deg": 170, "visible": True},
                    {"t": 2, "x": 2, "y": 5, "facing_deg": -170, "visible": False},
                ],
            }
        ],
    )
    a = sc.actors[0]
    x, y, facing, vis = g.interp_actor(a, 1.0)
    assert (x, y) == pytest.approx((1, 5))
    assert g.wrap180(facing) == pytest.approx(-180)  # shortest arc through 180, not through 0
    assert vis is True  # tie → earlier key
    assert g.interp_actor(a, 1.5)[3] is False
    assert g.interp_actor(a, 0.4)[3] is True


# --------------------------------------------------------------------------- distance / objects


def test_distance() -> None:
    assert g.distance((1, 1, 45), (4, 5)) == pytest.approx(5)


def test_object_points_rect_rotation() -> None:
    o = _obj(center=[0, 0], size=[2, 1], rotation_deg=90)
    pts = g.object_points(o)
    assert pts[0] == (0, 0)
    corners = sorted((round(x, 9), round(y, 9)) for x, y in pts[1:])
    # 2 wide × 1 deep rotated 90° → 1 wide × 2 deep
    assert corners == sorted([(0.5, -1.0), (0.5, 1.0), (-0.5, 1.0), (-0.5, -1.0)])


def test_object_points_circle() -> None:
    o = _obj(shape="circle", center=[1, 1], size=[2, 2])
    xs = [p[0] for p in g.object_points(o)]
    ys = [p[1] for p in g.object_points(o)]
    assert (min(xs), max(xs), min(ys), max(ys)) == pytest.approx((0, 2, 0, 2))


def test_object_bearing_range() -> None:
    o = _obj(center=[0, 5], size=[2, 0])
    lo, hi = g.object_bearing_range(ORIGIN, o)
    b = math.degrees(math.atan2(1, 5))
    assert (lo, hi) == pytest.approx((-b, b))


def test_object_bearing_range_behind_seam_is_contiguous() -> None:
    o = _obj(center=[0, -5], size=[2, 0])  # straight behind: bearings around ±180
    lo, hi = g.object_bearing_range(ORIGIN, o)
    assert hi - lo == pytest.approx(2 * math.degrees(math.atan2(1, 5)))


# --------------------------------------------------------------------------- camera_motion


def test_camera_motion_dolly_in() -> None:
    sc = _scene(
        [{"t": 0, "x": 0, "y": 0, "yaw_deg": 0}, {"t": 2, "x": 0, "y": 2, "yaw_deg": 0}],
        objects=[{"id": "o", "type": "box", "visual_description": "a box", "center": [0, 8], "size": [1, 1]}],
    )
    m = g.camera_motion(sc, 0, 2)
    assert m["forward"] == pytest.approx(2)
    assert m["lateral"] == pytest.approx(0)
    assert m["dyaw"] == pytest.approx(0)
    assert m["mean_scene_dist"] == pytest.approx(8)
    assert g.camera_motion(sc, 2, 0)["forward"] == pytest.approx(-2)  # reversed = dolly out


def test_camera_motion_in_t0_frame() -> None:
    # camera faces -x (yaw 90) and moves toward -x: that is forward, not lateral
    sc = _scene([{"t": 0, "x": 0, "y": 0, "yaw_deg": 90}, {"t": 1, "x": -3, "y": 0, "yaw_deg": 90}])
    m = g.camera_motion(sc, 0, 1)
    assert m["forward"] == pytest.approx(3)
    assert m["lateral"] == pytest.approx(0, abs=1e-9)


def test_camera_motion_truck_right_is_positive_lateral() -> None:
    sc = _scene([{"t": 0, "x": 0, "y": 0, "yaw_deg": 0}, {"t": 1, "x": 2, "y": 0, "yaw_deg": 0}])
    m = g.camera_motion(sc, 0, 1)
    assert m["lateral"] == pytest.approx(2)
    assert m["forward"] == pytest.approx(0, abs=1e-9)


def test_camera_motion_pan_over_180() -> None:
    track = [{"t": t, "x": 0, "y": 0, "yaw_deg": 60 * t} for t in range(5)]  # 0 → 240° left
    m = g.camera_motion(_scene(track), 0, 4)
    assert m["dyaw"] == pytest.approx(240)
    assert m["dyaw"] > 0  # left


def test_camera_motion_empty_scene() -> None:
    sc = _scene([{"t": 0, "x": 0, "y": 0}])
    assert g.camera_motion(sc, 0, 1)["mean_scene_dist"] == 0.0


# --------------------------------------------------------------------------- mock cross-check (S00)


def _entity_point(scene: SceneV1, eid: str, t: float) -> tuple[float, float]:
    e = scene.entity(eid)
    if isinstance(e, Obj):
        return e.center[0], e.center[1]
    x, y, _, _ = g.interp_actor(e, t)
    return x, y


def test_mock_door_is_left_and_out_of_view_early(scene_mock: SceneV1) -> None:
    cam = g.interp_camera(scene_mock, 2.0)
    door = _entity_point(scene_mock, "door_1", 2.0)
    assert g.bearing_deg(cam, door) > 0  # camera's left
    assert not g.in_view(cam, door, scene_mock.camera.hfov_deg)  # 38.7° > 30° at t=2
    late = g.interp_camera(scene_mock, 8.0)  # after the 40° pan left it comes into view
    assert g.in_view(late, door, scene_mock.camera.hfov_deg)


def _check_geometry(scene: SceneV1, c: CheckV1) -> tuple[object, float]:
    """Recompute each mock check's expected answer and margin from geometry alone."""
    hfov = scene.camera.hfov_deg
    cam = g.interp_camera(scene, c.t)
    pts = [_entity_point(scene, e, c.t) for e in c.entities]
    if c.template in ("VISIBLE", "NOT_VISIBLE"):
        b = abs(g.bearing_deg(cam, pts[0]))
        return b < hfov / 2, abs(b - hfov / 2)
    if c.template in ("SIDE_LEFT", "SIDE_RIGHT"):
        b = g.bearing_deg(cam, pts[0])
        on_left = b > 0
        return on_left == (c.template == "SIDE_LEFT"), abs(b)
    if c.template == "A_LEFT_OF_B":
        ba, bb = (g.bearing_deg(cam, p) for p in pts)
        return ba > bb, abs(ba - bb)
    if c.template == "CLOSER":
        da, db = (g.distance(cam, p) for p in pts)
        return ("a_first" if da < db else "b_second"), max(da, db) / min(da, db)
    assert c.t_end is not None
    if c.template == "TOWARD":
        cam1 = g.interp_camera(scene, c.t_end)
        d0 = g.distance(cam, pts[0])
        d1 = g.distance(cam1, _entity_point(scene, c.entities[0], c.t_end))
        return d1 < d0, abs(d1 - d0) / d0 * 100
    m = g.camera_motion(scene, c.t, c.t_end)
    if c.template == "PAN":
        return ("pan_left" if m["dyaw"] > 0 else "pan_right"), abs(m["dyaw"])
    if c.template == "DOLLY":
        return ("dolly_in" if m["forward"] > 0 else "dolly_out"), abs(m["forward"])
    raise AssertionError(c.template)


def test_mock_checks_match_geometry(scene_mock: SceneV1, checks_mock: list[CheckV1]) -> None:
    assert checks_mock, "mock checks should not be empty"
    for c in checks_mock:
        expected, margin = _check_geometry(scene_mock, c)
        assert expected == c.expected, f"{c.check_id} {c.template}: geometry says {expected}"
        assert margin == pytest.approx(c.margin, abs=0.1), f"{c.check_id} {c.template} margin"
