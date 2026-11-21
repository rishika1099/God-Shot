"""S07 tests: geometry of the drawing (pure, Pillow only) + an ffmpeg encode (skipped without ffmpeg)."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from godshot import render, style
from godshot.schema import CamKey, Clip, LowConf, SceneV1

SIZE = (1080, 960)
CLIP = Clip(id="t", duration_s=2.0, fps=10.0, width=640, height=360)
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg")


def _cam_scene(yaw: float, low: bool = False) -> SceneV1:
    return SceneV1(
        clip=CLIP,
        room={"outline": [[-5, -5], [5, -5], [5, 5], [-5, 5]]},
        camera={"hfov_deg": 60, "track": [CamKey(t=0, x=0, y=0, yaw_deg=yaw)]},
        low_confidence=[LowConf(entity_id="camera", t0=0, t1=2)] if low else [],
    )


def _green(px: tuple[int, ...]) -> int:
    """How much greener than the background a pixel is."""
    return px[1] - max(px[0], px[2])


def _sample(scene: SceneV1, img, deg: float, dist: float = 2.5) -> tuple[int, ...]:
    view = render.fit_view(render.scene_bounds(scene), SIZE)
    hx, hy = render.heading(deg)
    x, y = view.px(hx * dist, hy * dist)
    return img.getpixel((round(x), round(y)))


# --------------------------------------------------------------------------- pure helpers


def test_heading_convention():
    assert render.heading(0) == pytest.approx((0, 1))
    assert render.heading(90) == pytest.approx((-1, 0))  # + yaw = turned to the camera's LEFT
    assert render.heading(-90) == pytest.approx((1, 0))


def test_view_maps_y_up_and_keeps_aspect():
    view = render.fit_view((0, 0, 10, 5), SIZE)
    (x0, y0), (x1, y1) = view.px(0, 0), view.px(10, 5)
    assert x1 > x0 and y1 < y0  # +x right, +y up on screen
    assert (x1 - x0) / (y0 - y1) == pytest.approx(2.0)
    margin = (x0, SIZE[0] - x1)
    assert margin[0] == pytest.approx(margin[1]) and margin[0] > 0  # 10% margin, centred


def test_bounds_cover_room_and_tracks(scene_mock):
    # room is [-4, 4] × [0, 9]; the door (0.2 wide on x=-4) and window (0.2 deep on y=9) stick out 0.1
    assert render.scene_bounds(scene_mock) == pytest.approx((-4.1, 0.0, 4.0, 9.1))


def test_motion_label(scene_mock):
    assert render.motion_label(scene_mock, 1.0) == "DOLLY IN"
    assert render.motion_label(scene_mock, 6.0) == "PAN LEFT"
    assert render.motion_label(scene_mock, 9.0) == "STATIC"


def test_fov_wedge_spans_hfov():
    w = render.fov_wedge(0, 0, 30, 60, 4)
    angles = [math.degrees(math.atan2(-x, y)) for x, y in w[1:]]
    assert min(angles) == pytest.approx(0) and max(angles) == pytest.approx(60)
    assert all(math.hypot(x, y) == pytest.approx(4) for x, y in w[1:])


def test_obj_color_fallback_by_type(scene_mock):
    obj = scene_mock.objects[0].model_copy(update={"color": "#888888", "type": "Door"})
    assert render.obj_color(obj) == style.OBJ_PALETTE["door"]
    assert render.obj_color(scene_mock.objects[0]) == scene_mock.objects[0].color


def test_frame_count_matches_clip(scene_mock):
    assert render.frame_count(scene_mock) == 240  # 10 s × 24 fps
    assert render.frame_count(scene_mock, fps=30) == 300


# --------------------------------------------------------------------------- drawn pixels


@pytest.mark.parametrize("yaw", [0, 30, -30, 90, 150])
def test_fov_wedge_points_where_yaw_says(yaw):
    scene = _cam_scene(yaw)
    img = render.render_frame(scene, 0.5, SIZE)
    ahead, mirrored = _sample(scene, img, yaw), _sample(scene, img, yaw + 180)
    assert _green(ahead) > 20
    assert _green(mirrored) < 5


def test_yaw_plus_30_is_turned_left():
    scene = _cam_scene(30)
    img = render.render_frame(scene, 0.5, SIZE)
    view = render.fit_view(render.scene_bounds(scene), SIZE)
    cx, cy = view.px(0, 0)
    left = img.getpixel((round(cx - 60), round(cy - 120)))
    right = img.getpixel((round(cx + 60), round(cy - 120)))
    assert _green(left) > 20 > _green(right)


def test_low_confidence_camera_dims_wedge():
    normal = render.render_frame(_cam_scene(0), 0.5, SIZE)
    low = render.render_frame(_cam_scene(0, low=True), 0.5, SIZE)
    s = _cam_scene(0)
    assert _green(_sample(s, low, 0)) < _green(_sample(s, normal, 0))


def test_hidden_actor_not_drawn(scene_mock):
    a = scene_mock.actors[0]
    hidden = scene_mock.model_copy(deep=True)
    for k in hidden.actors[0].track:
        k.visible = False
    t = 5.0
    x, y, _, _ = render.interp_actor(a, t)
    view = render.fit_view(render.scene_bounds(scene_mock), SIZE)
    at = tuple(round(v) for v in view.px(x, y))
    shown_px = render.render_frame(scene_mock, t, SIZE).getpixel(at)
    hidden_px = render.render_frame(hidden, t, SIZE).getpixel(at)
    assert shown_px[0] > 150 and hidden_px[0] < 100  # red head vs background


def test_ghost_and_low_conf_render(scene_mock):
    ghost = scene_mock.model_copy(deep=True)
    for k in ghost.camera.track:
        k.x += 1.5
    s = scene_mock.model_copy(update={"low_confidence": [LowConf(entity_id="door_1", t0=0, t1=10)]})
    img = render.render_frame(s, 3.0, SIZE, ghost=ghost)
    assert img.size == SIZE and img.mode == "RGB"


def test_render_speed(scene_mock):
    prepared = render._prepare(scene_mock, SIZE, None)
    t0 = time.perf_counter()
    for i in range(60):
        render.render_frame(scene_mock, i / 24, SIZE, _static=prepared)
    assert 60 / (time.perf_counter() - t0) >= 60  # spec target: >= 60 frames/s


# --------------------------------------------------------------------------- video (ffmpeg)


@needs_ffmpeg
def test_render_map_mock_frame_count_and_speed(scene_mock, tmp_path: Path):
    out = tmp_path / "map.mp4"
    t0 = time.perf_counter()
    render.render_map(scene_mock, out)
    assert time.perf_counter() - t0 < 15
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
         "stream=nb_read_frames,width,height,r_frame_rate", "-of", "json", str(out)],
        check=True, capture_output=True, text=True,
    )
    st = json.loads(probe.stdout)["streams"][0]
    assert int(st["nb_read_frames"]) == render.frame_count(scene_mock) == 240
    assert (st["width"], st["height"]) == SIZE
    assert st["r_frame_rate"] == "24/1"


def test_odd_size_rejected(scene_mock, tmp_path: Path):
    with pytest.raises(ValueError):
        render.render_map(scene_mock, tmp_path / "x.mp4", size=(1081, 960))
