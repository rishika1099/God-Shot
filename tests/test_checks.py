"""Unit & property tests for S04 (godshot/checks.py)."""

from __future__ import annotations

import copy
import math
import random
from pathlib import Path

from godshot.checks import (
    CLOSER_MIN_RATIO,
    DOLLY_MIN_REL_DIST,
    ORDER_MARGIN_DEG,
    PAN_MIN_DYAW_DEG,
    SIDE_MARGIN_DEG,
    TOWARD_MIN_REL_CHANGE,
    VISIBLE_MARGIN_DEG,
    _bearing_deg,
    _camera_motion,
    _distance,
    _interp_actor,
    _interp_camera,
    frames_needed,
    make_checks,
    predict,
)
from godshot.schema import (
    Actor,
    ActorKey,
    Camera,
    CamKey,
    Clip,
    MotionSegment,
    Obj,
    Room,
    SceneV1,
    load_checks,
    qkey,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MOCK_SCENE = REPO_ROOT / "mocks" / "scene_mock.json"
MOCK_CHECKS = REPO_ROOT / "mocks" / "checks_mock.json"


def _random_scene(seed: int) -> SceneV1:
    rng = random.Random(seed)
    duration = rng.choice([6.0, 8.0, 10.0])
    cam_keys = []
    cx, cy, yaw = 0.0, 0.0, 0.0
    dx = rng.uniform(-0.3, 0.3)
    dy = rng.uniform(-0.4, 0.6)
    dyaw = rng.uniform(-12.0, 12.0)
    for sec in range(int(duration) + 1):
        cam_keys.append(CamKey(t=float(sec), x=round(cx, 2), y=round(cy, 2), yaw_deg=round(yaw, 1)))
        cx += dx
        cy += dy
        yaw = ((yaw + dyaw + 180.0) % 360.0) - 180.0

    objects = [
        Obj(
            id=f"obj_{i}",
            type="prop",
            visual_description=f"the distinctive object number {i}",
            center=[round(rng.uniform(-4.5, 4.5), 2), round(rng.uniform(1.5, 9.5), 2)],
            size=[0.8, 0.8],
        )
        for i in range(3)
    ]

    ax, ay = rng.uniform(-3.0, 3.0), rng.uniform(3.0, 8.0)
    vx, vy = rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5)
    actor_keys = [
        ActorKey(
            t=float(sec),
            x=round(ax + vx * sec, 2),
            y=round(max(0.5, ay + vy * sec), 2),
            visible=True,
        )
        for sec in range(int(duration) + 1)
    ]
    actors = [
        Actor(id="actor_0", visual_description="the person wearing a bright yellow coat", track=actor_keys)
    ]

    return SceneV1(
        clip=Clip(id=f"rand_{seed}", duration_s=duration, fps=24.0, width=1280, height=720),
        room=Room(outline=[[-5.0, 0.0], [5.0, 0.0], [5.0, 10.0], [-5.0, 10.0]]),
        objects=objects,
        actors=actors,
        camera=Camera(
            hfov_deg=rng.choice([50.0, 60.0, 70.0]),
            track=cam_keys,
            motion_segments=[
                MotionSegment(t0=0.0, t1=duration / 2.0, translation="dolly_in", rotation="none")
            ],
        ),
    )


def test_mock_scene_produces_valid_checks_and_all_templates() -> None:
    scene = SceneV1.load(MOCK_SCENE)
    checks = make_checks(scene, Path("frames/mock_001"))

    assert 20 <= len(checks) <= 60
    templates = {c.template for c in checks}
    expected_templates = {
        "VISIBLE",
        "NOT_VISIBLE",
        "SIDE_LEFT",
        "SIDE_RIGHT",
        "A_LEFT_OF_B",
        "CLOSER",
        "TOWARD",
        "PAN",
        "DOLLY",
    }
    assert expected_templates.issubset(templates)

    # Mirrored controls exist with expected=False
    controls = [c for c in checks if c.control]
    assert len(controls) >= 1
    assert all(c.template in ("SIDE_LEFT", "SIDE_RIGHT") and c.expected is False for c in controls)

    # No duplicate qkeys
    qkeys = [qkey(c) for c in checks]
    assert len(qkeys) == len(set(qkeys))


def test_spot_checked_expectations_match_mock_checks_and_hand_reasoning() -> None:
    scene = SceneV1.load(MOCK_SCENE)
    mock_checks = load_checks(MOCK_CHECKS)

    # Every single check in mocks/checks_mock.json must match predict(scene, c) == c.expected!
    assert len(mock_checks) >= 5
    for c in mock_checks:
        assert predict(scene, c) == c.expected, f"mismatch on {c.check_id} ({c.template} @ t={c.t})"


def test_instructions_are_full_sentences_and_never_contain_ids() -> None:
    scene = SceneV1.load(MOCK_SCENE)
    checks = make_checks(scene, Path("frames/mock_001"))
    forbidden_ids = [e.id for e in [*scene.objects, *scene.actors]]

    for c in checks:
        text = c.question.instructions
        assert len(text) >= 20
        assert text[0].isupper()
        assert text.endswith("?")
        for eid in forbidden_ids:
            # Check whole token / id substring doesn't leak
            assert eid not in text.split(), f"entity id {eid!r} leaked into {text!r}"
            assert f" {eid}" not in text and f"{eid} " not in text
        if c.question.options:
            for desc in c.question.options.values():
                for eid in forbidden_ids:
                    assert eid not in desc.split()


def test_predict_equals_expected_on_mock_and_random_scenes() -> None:
    scenes = [SceneV1.load(MOCK_SCENE), *(_random_scene(s) for s in range(25))]
    for scene in scenes:
        checks = make_checks(scene, Path(f"frames/{scene.clip.id}"))
        qkeys = [qkey(c) for c in checks]
        assert len(qkeys) == len(set(qkeys)), "duplicate qkeys emitted"
        for c in checks:
            pred = predict(scene, c)
            assert pred == c.expected, f"{scene.clip.id}: {c.template} expected {c.expected}, got {pred}"
            assert pred != "other"


def test_no_check_emitted_inside_abstain_margin_random_poses() -> None:
    for seed in range(25):
        scene = _random_scene(seed)
        checks = make_checks(scene, Path(f"frames/{scene.clip.id}"))
        half = scene.camera.hfov_deg / 2.0

        def pos(eid: str, t: float, s: SceneV1 = scene) -> tuple[float, float]:
            ent = s.entity(eid)
            if isinstance(ent, Obj):
                return (ent.center[0], ent.center[1])
            x, y, _, _ = _interp_actor(ent, t)
            return (x, y)

        for c in checks:
            cam = _interp_camera(scene, c.t)
            if c.template == "VISIBLE":
                b = abs(_bearing_deg(cam, pos(c.entities[0], c.t)))
                assert b < half - VISIBLE_MARGIN_DEG
            elif c.template == "NOT_VISIBLE":
                b = abs(_bearing_deg(cam, pos(c.entities[0], c.t)))
                assert b > half + VISIBLE_MARGIN_DEG
            elif c.template in ("SIDE_LEFT", "SIDE_RIGHT"):
                b = abs(_bearing_deg(cam, pos(c.entities[0], c.t)))
                assert SIDE_MARGIN_DEG < b < half
            elif c.template == "A_LEFT_OF_B":
                ba = _bearing_deg(cam, pos(c.entities[0], c.t))
                bb = _bearing_deg(cam, pos(c.entities[1], c.t))
                assert abs(ba - bb) > ORDER_MARGIN_DEG
            elif c.template == "CLOSER":
                da = _distance(cam, pos(c.entities[0], c.t))
                db = _distance(cam, pos(c.entities[1], c.t))
                assert max(da, db) / min(da, db) > CLOSER_MIN_RATIO
            elif c.template == "TOWARD":
                assert c.t_end is not None
                cam1 = _interp_camera(scene, c.t_end)
                d0 = _distance(cam, pos(c.entities[0], c.t))
                d1 = _distance(cam1, pos(c.entities[0], c.t_end))
                assert abs((d0 - d1) / d0) > TOWARD_MIN_REL_CHANGE
            elif c.template == "PAN":
                assert c.t_end is not None
                m = _camera_motion(scene, c.t, c.t_end)
                assert abs(m["dyaw"]) > PAN_MIN_DYAW_DEG
            elif c.template == "DOLLY":
                assert c.t_end is not None
                m = _camera_motion(scene, c.t, c.t_end)
                assert abs(m["forward"]) > DOLLY_MIN_REL_DIST * m["mean_scene_dist"]


def test_predict_cross_scene_mutations() -> None:
    scene = SceneV1.load(MOCK_SCENE)
    mock_checks = load_checks(MOCK_CHECKS)
    by_tmpl = {c.template: c for c in mock_checks if not c.control}

    # 1. Moving an object/actor across the midline flips SIDE_LEFT
    side_left = next(c for c in mock_checks if c.template == "SIDE_LEFT" and not c.control)
    assert predict(scene, side_left) is True  # a1 at (-2.0, 6.5), cam at (0, 0.5)
    flipped = copy.deepcopy(scene)
    for k in flipped.actors[0].track:
        k.x = 2.0  # move clearly to camera's right
    assert predict(flipped, side_left) is False

    # 2. Moving into abstain margin returns None (|bearing| <= 6°)
    centered = copy.deepcopy(scene)
    for k in centered.actors[0].track:
        k.x = 0.0  # dead ahead => bearing 0°
    assert predict(centered, side_left) is None

    # 3. Setting visible=False returns None
    hidden = copy.deepcopy(scene)
    for k in hidden.actors[0].track:
        k.visible = False
    assert predict(hidden, side_left) is None
    assert predict(hidden, by_tmpl["TOWARD"]) is None

    # 4. Deleting an entity returns None
    deleted = copy.deepcopy(scene)
    deleted.actors = []
    assert predict(deleted, side_left) is None
    assert predict(deleted, by_tmpl["A_LEFT_OF_B"]) is None

    # 5. Static camera answers PAN and DOLLY with None (never "other")
    static_scene = copy.deepcopy(scene)
    for k in static_scene.camera.track:
        k.x = 0.0
        k.y = 0.0
        k.yaw_deg = 0.0
    assert predict(static_scene, by_tmpl["PAN"]) is None
    assert predict(static_scene, by_tmpl["DOLLY"]) is None

    # 6. NOT_VISIBLE outside hfov/2 + 8° is a prediction (False), not None
    not_vis = next(c for c in mock_checks if c.template == "NOT_VISIBLE" and c.entities == ["door_1"])
    assert predict(scene, not_vis) is False
    # Moving door_1 into the ±8° band around 30° returns None
    edge = copy.deepcopy(scene)
    # At t=3.0, cam=(0, 1.5, yaw=0); bearing 30° at dy=4.5 => x = -4.5*tan(30°) ≈ -2.60
    edge.objects[0].center = [-round(4.5 * math.tan(math.radians(30.0)), 2), 6.0]
    assert predict(edge, not_vis) is None


def test_frames_needed_sorted_unique() -> None:
    mock_checks = load_checks(MOCK_CHECKS)
    ts = frames_needed(mock_checks)
    assert ts == sorted(set(ts))
    assert 0.0 in ts and 4.0 in ts and 8.0 in ts and 9.0 in ts
