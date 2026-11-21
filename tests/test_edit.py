"""S09 edit tests: drag ops, move presets (geometry-checked), text edit via a fake Hydrogen client."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from godshot import edit as ed
from godshot import geometry as g
from godshot.config import load_config
from godshot.schema import SceneV1


def _static_scene(duration: float = 10.0) -> SceneV1:
    return SceneV1.model_validate(
        {
            "clip": {"id": "s", "duration_s": duration, "fps": 24, "width": 1280, "height": 720},
            "objects": [
                {
                    "id": "t1",
                    "type": "table",
                    "visual_description": "a table",
                    "center": [0, 6],
                    "size": [1, 1],
                }
            ],
            "actors": [
                {
                    "id": "a1",
                    "visual_description": "a person",
                    "track": [{"t": 0, "x": 0, "y": 5}, {"t": duration, "x": 0, "y": 5}],
                }
            ],
            "camera": {
                "hfov_deg": 60,
                "track": [{"t": t, "x": 0, "y": 0, "yaw_deg": 0} for t in range(11)],
                "motion_segments": [{"t0": 0, "t1": duration}],
            },
        }
    )


# --------------------------------------------------------------------------- presets


def test_dolly_in_moves_forward_and_carries_the_rest() -> None:
    sc = ed.apply_preset(_static_scene(), 0, 4, "dolly_in", amount=2.0)
    m = g.camera_motion(sc, 0, 4)
    assert m["forward"] == pytest.approx(2.0) and m["lateral"] == pytest.approx(0, abs=1e-6)
    assert g.interp_camera(sc, 7)[:2] == pytest.approx((0, 2.0))  # later keys follow from the new end pose
    segs = [(s.t0, s.t1, s.translation, s.rotation) for s in sc.camera.motion_segments]
    assert segs == [(0, 4, "dolly_in", "none"), (4, 10, "static", "none")]


def test_pan_left_rotates_in_place() -> None:
    sc = ed.apply_preset(_static_scene(), 2, 5, "pan_left", amount=30)
    m = g.camera_motion(sc, 2, 5)
    assert m["dyaw"] == pytest.approx(30) and abs(m["forward"]) < 1e-6
    assert sc.camera.motion_segments[1].rotation == "pan_left"
    assert g.interp_camera(sc, 9)[2] == pytest.approx(30)  # stays turned afterwards


def test_truck_right() -> None:
    m = g.camera_motion(ed.apply_preset(_static_scene(), 0, 4, "truck_right", amount=1.5), 0, 4)
    assert m["lateral"] == pytest.approx(1.5) and m["dyaw"] == pytest.approx(0)


@pytest.mark.parametrize("move", ["arc_right", "arc_left"])
def test_arc_orbits_the_actor_and_keeps_it_framed(move: str) -> None:
    base = _static_scene()
    sc = ed.apply_preset(base, 0, 10, move, amount=40, target="a1")
    m = g.camera_motion(sc, 0, 10)
    sign = 1 if move == "arc_right" else -1
    assert sign * m["lateral"] > 1.0  # moved to its right (arc_right) / left (arc_left)
    assert m["dyaw"] == pytest.approx(sign * 40)  # turned the other way on screen to keep the subject
    actor = sc.actors[0]
    for t in (0, 2.5, 5, 7.5, 10):
        cam = g.interp_camera(sc, t)
        x, y, _, _ = g.interp_actor(actor, t)
        assert abs(g.bearing_deg(cam, (x, y))) < 1.0  # actor stays centred
        assert g.distance(cam, (x, y)) == pytest.approx(5.0, abs=0.05)  # constant radius
    assert sc.camera.motion_segments[0].translation == move


def test_arc_defaults_to_first_actor_and_rejects_unknown_target() -> None:
    sc = ed.apply_preset(_static_scene(), 0, 4, "arc_right")
    assert g.camera_motion(sc, 0, 4)["lateral"] > 0
    with pytest.raises(ed.EditError):
        ed.apply_preset(_static_scene(), 0, 4, "arc_right", target="nobody")


def test_preset_validation() -> None:
    with pytest.raises(ed.EditError):
        ed.apply_preset(_static_scene(), 0, 4, "zoom_in")
    with pytest.raises(ed.EditError):
        ed.apply_preset(_static_scene(), 3, 3.1, "dolly_in")


def test_preset_on_mock_splits_segments(scene_mock: SceneV1) -> None:
    sc = ed.apply_preset(scene_mock, 2, 6, "truck_left")
    segs = [(s.t0, s.t1, s.translation, s.rotation) for s in sc.camera.motion_segments]
    assert segs == [
        (0, 2, "dolly_in", "none"),
        (2, 6, "truck_left", "none"),
        (6, 8, "static", "pan_left"),
        (8, 10, "static", "none"),
    ]
    SceneV1.model_validate_json(sc.model_dump_json(by_alias=True))  # still a valid scene.v1


# --------------------------------------------------------------------------- apply_edit ops


def test_apply_edit_ops_do_not_mutate_input(scene_mock: SceneV1) -> None:
    before = scene_mock.model_dump_json()
    s1 = ed.apply_edit(scene_mock, {"op": "move_camera_key", "index": 3, "x": 1.5, "y": 2.5})
    k = sorted(s1.camera.track, key=lambda k: k.t)[3]
    assert (k.x, k.y) == (1.5, 2.5)
    s2 = ed.apply_edit(s1, {"op": "set_yaw", "index": 3, "yaw_deg": 200})
    assert sorted(s2.camera.track, key=lambda k: k.t)[3].yaw_deg == pytest.approx(-160)
    assert ed.apply_edit(scene_mock, {"op": "set_fov", "hfov_deg": 500}).camera.hfov_deg == 120
    assert ed.apply_edit(scene_mock, {"op": "move_object", "id": "door_1", "x": -3, "y": 5}).objects[
        0
    ].center == [-3, 5]
    s3 = ed.apply_edit(scene_mock, {"op": "move_actor_key", "id": "a1", "index": 0, "x": -3, "y": 6})
    assert (s3.actors[0].track[0].x, s3.actors[0].track[0].y) == (-3, 6)
    assert scene_mock.model_dump_json() == before


@pytest.mark.parametrize(
    "bad",
    [
        {"op": "teleport"},
        {"op": "move_camera_key", "index": 99, "x": 0, "y": 0},
        {"op": "move_object", "id": "nope", "x": 0, "y": 0},
        {"op": "move_actor_key", "id": "nope", "index": 0, "x": 0, "y": 0},
    ],
)
def test_apply_edit_errors(scene_mock: SceneV1, bad: dict) -> None:
    with pytest.raises(ed.EditError):
        ed.apply_edit(scene_mock, bad)


def test_apply_edit_full_scene_is_validated(scene_mock: SceneV1) -> None:
    data = json.loads(scene_mock.model_dump_json(by_alias=True))
    data["camera"]["hfov_deg"] = 45
    assert ed.apply_edit(scene_mock, {"op": "scene", "scene": data}).camera.hfov_deg == 45
    data["camera"]["hfov_deg"] = 400
    with pytest.raises(ValueError):
        ed.apply_edit(scene_mock, {"op": "scene", "scene": data})


# --------------------------------------------------------------------------- text edit (fake Hydrogen)


@pytest.fixture
def tmp_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GODSHOT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GODSHOT_OFFLINE", raising=False)
    load_config.cache_clear()
    yield tmp_path / "cache"
    load_config.cache_clear()


class FakeHydrogen:
    def __init__(self, plan: dict) -> None:
        self.plan, self.calls = plan, []
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, *, model: str, contents: str, config: object) -> SimpleNamespace:
        self.calls.append(contents)
        return SimpleNamespace(text=json.dumps(self.plan))


def test_text_edit_slow_arc_right_around_actor(tmp_cache: Path, scene_mock: SceneV1) -> None:
    """Acceptance: "make it a slow arc to the right around the actor" changes the track sensibly."""
    fake = FakeHydrogen(
        {
            "presets": [{"move": "arc_right", "t0": 0, "t1": 10, "amount": 20, "target": "a1"}],
            "note": "slow arc",
        }
    )
    out = ed.text_edit(scene_mock, "make it a slow arc to the right around the actor", client=fake)
    prompt = fake.calls[0]
    assert "make it a slow arc" in prompt and "a1 = the man in the red hoodie" in prompt
    m = g.camera_motion(out, 0, 10)
    assert m["lateral"] > 0 and m["dyaw"] > 0  # orbit to the right
    assert [s.translation for s in out.camera.motion_segments] == ["arc_right"]
    assert "text edit" in out.model_notes

    again = ed.text_edit(scene_mock, "make it a slow arc to the right around the actor", client=fake)
    assert len(fake.calls) == 1  # cached
    assert again.camera.track == out.camera.track


def test_text_edit_skips_bad_steps_and_sets_fov(tmp_cache: Path, scene_mock: SceneV1) -> None:
    fake = FakeHydrogen(
        {
            "presets": [
                {"move": "arc_left", "t0": 0, "t1": 5, "target": "ghost"},
                {"move": "dolly_in", "t0": 5, "t1": 8},
            ],
            "hfov_deg": 40,
        }
    )
    out = ed.text_edit(scene_mock, "tighter lens and push in at the end", client=fake)
    assert out.camera.hfov_deg == 40
    assert g.camera_motion(out, 5, 8)["forward"] > 0


def test_text_edit_offline_miss_raises(
    tmp_cache: Path, scene_mock: SceneV1, monkeypatch: pytest.MonkeyPatch
) -> None:
    from godshot.cache import CacheMiss

    monkeypatch.setenv("GODSHOT_OFFLINE", "1")
    with pytest.raises(CacheMiss):
        ed.text_edit(scene_mock, "dolly in", client=FakeHydrogen({}))
