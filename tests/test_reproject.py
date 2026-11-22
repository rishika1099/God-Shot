"""Unit tests for godshot.reproject (pure geometry/projection/prop math; no heavy model downloads)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from godshot import reproject, reshoot
from godshot.schema import SceneV1

MOCK = Path(__file__).resolve().parent.parent / "mocks" / "scene_mock.json"


def test_unproject_and_splat_roundtrip() -> None:
    """Unprojecting a constant-depth patch from cam0 and splatting back to cam0 reproduces the pixels."""
    h, w = 60, 80
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    rgb[20:40, 30:50] = (200, 50, 40)
    z_map = np.full((h, w), 3.0, dtype=np.float32)
    cam = (0.0, 0.0, 0.0)
    xyz, col = reproject._unproject_pixels(rgb, z_map, cam, 60.0)
    canvas = np.zeros((h, w, 3), dtype=np.uint8)
    zbuf = np.full((h, w), 1e6, dtype=np.float32)
    reproject._splat_points(xyz, col, cam, 60.0, canvas, zbuf, radius=0)
    assert tuple(canvas[30, 40]) == (200, 50, 40)
    assert abs(float(zbuf[30, 40]) - 3.0) < 0.05


def test_fit_depth_scale_is_monotonic_and_positive() -> None:
    scene = SceneV1.load(MOCK)
    h, w = 90, 160
    disp = np.linspace(1.0, 10.0, h * w, dtype=np.float32).reshape(h, w)
    a, b, z_max = reproject._fit_depth_scale(disp, scene, 0.0)
    assert a > 0 and b > 0 and z_max > 2.0
    z_map = reproject._disp_to_metric_depth(disp, a, b, z_max)
    assert float(z_map.min()) >= 0.34 and float(z_map.max()) <= z_max + 0.01
    # Higher disparity = closer depth
    assert float(z_map[-1, -1]) < float(z_map[0, 0])


def test_impact_time_and_clean_3d_pose_frame_with_hinged_laptop() -> None:
    scene = SceneV1.load(MOCK)
    # Add a laptop object on top of the dining table and a punch beat
    laptop = scene.objects[0].model_copy(
        update={
            "id": "obj_laptop",
            "type": "laptop",
            "visual_description": "black gaming laptop with green screen",
            "size": [0.45, 0.35],
        }
    )
    scene.objects.append(laptop)
    beats = [
        {"t0": 3.5, "t1": 4.5, "who": scene.actors[0].visual_description, "action": "punches laptop flat"}
    ]
    t_imp = reproject._impact_time_for_prop(laptop, beats)
    assert t_imp is not None and 3.9 <= t_imp <= 4.3
    colors = reshoot.blockout_palette(scene)
    img_before = reproject._render_clean_3d_pose_frame(scene, scene, 1.0, 320, 180, colors, None, beats)
    img_after = reproject._render_clean_3d_pose_frame(scene, scene, 5.0, 320, 180, colors, None, beats)
    assert img_before.size == (320, 180) and img_after.size == (320, 180)
    # Hinged laptop visibly changes between open (1.0s) and smashed flat (5.0s)
    assert not np.array_equal(np.asarray(img_before), np.asarray(img_after))


def test_edit_prompt_reproject_mode_includes_novel_view_brief() -> None:
    scene = SceneV1.load(MOCK)
    p = reshoot.edit_prompt(
        scene,
        reshoot.blockout_palette(scene),
        reproject_mode=True,
        novel_view_brief="Show the back of the laptop lid collapsing flat at 00:04.2.",
    )
    assert "3D-reprojected novel-view video" in p
    assert "NOVEL-VIEW ANALYSIS: Show the back of the laptop lid collapsing flat at 00:04.2." in p
