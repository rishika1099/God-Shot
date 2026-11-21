"""Offline unit tests for S06 (correct.py + pipeline.py shared question bank loop)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from godshot.checks import make_checks, predict
from godshot.correct import build_correction_prompt, freeze_identity, mark_low_confidence
from godshot.pipeline import run_loop
from godshot.schema import (
    Actor,
    ActorKey,
    Camera,
    CamKey,
    CheckResult,
    CheckV1,
    Clip,
    Obj,
    QKey,
    Room,
    SceneV1,
    load_checks,
    qkey,
)


def _make_simple_scene(
    *,
    door_x: float = -2.0,
    table_x: float = 2.0,
    lamp_x: float = -1.5,
    include_door: bool = True,
) -> SceneV1:
    """Clean 3-second static-camera scene where objects sit at known bearings."""
    objects = []
    if include_door:
        objects.append(
            Obj(
                id="door_1",
                type="door",
                visual_description="the green wooden door",
                center=[door_x, 5.0],
                size=[0.4, 1.4],
            )
        )
    objects.extend(
        [
            Obj(
                id="table_1",
                type="table",
                visual_description="the round white dining table",
                center=[table_x, 5.0],
                size=[1.0, 1.0],
            ),
            Obj(
                id="lamp_1",
                type="lamp",
                visual_description="the tall brass floor lamp",
                center=[lamp_x, 8.0],
                size=[0.4, 0.4],
            ),
        ]
    )
    return SceneV1(
        clip=Clip(id="test_loop_clip", duration_s=3.0, fps=24.0, width=1280, height=720),
        room=Room(outline=[[-4.0, 0.0], [4.0, 0.0], [4.0, 9.0], [-4.0, 9.0]]),
        objects=objects,
        actors=[
            Actor(
                id="a1",
                visual_description="the woman in the blue coat",
                track=[
                    ActorKey(t=0.0, x=1.5, y=7.0, visible=True),
                    ActorKey(t=3.0, x=1.5, y=7.0, visible=True),
                ],
            )
        ],
        camera=Camera(
            hfov_deg=60.0,
            track=[
                CamKey(t=0.0, x=0.0, y=0.0, yaw_deg=0.0),
                CamKey(t=3.0, x=0.0, y=0.0, yaw_deg=0.0),
            ],
        ),
    )


def _oracle_verifier(ground_truth: SceneV1):
    """Fake verifier that answers every check from `ground_truth` and tracks verified qkeys."""
    verified_calls: list[QKey] = []

    def _verify(checks: list[CheckV1], _frames_dir: Path) -> dict[QKey, CheckResult]:
        out: dict[QKey, CheckResult] = {}
        for c in checks:
            k = qkey(c)
            verified_calls.append(k)
            ans = predict(ground_truth, c)
            if c.question.type == "noul":
                if ans is True:
                    out[k] = CheckResult(p_yes=0.92, confidence=0.92, verdict=None)
                elif ans is False:
                    out[k] = CheckResult(p_yes=0.08, confidence=0.92, verdict=None)
                else:
                    out[k] = CheckResult(p_yes=0.50, confidence=0.50, verdict=None)
            else:
                opts = list((c.question.options or {}).keys())
                if isinstance(ans, str) and ans in opts:
                    probs = {o: (0.85 if o == ans else 0.15 / max(len(opts) - 1, 1)) for o in opts}
                    out[k] = CheckResult(choice_probs=probs, confidence=0.85, verdict=None)
                else:
                    probs = {o: (0.70 if o == "other" else 0.15) for o in opts}
                    out[k] = CheckResult(choice_probs=probs, confidence=0.70, verdict=None)
        return out

    return _verify, verified_calls


def _make_door_only_scene(door_x: float) -> SceneV1:
    return SceneV1(
        clip=Clip(id="test_door_only", duration_s=3.0, fps=24.0, width=1280, height=720),
        room=Room(outline=[[-4.0, 0.0], [4.0, 0.0], [4.0, 9.0], [-4.0, 9.0]]),
        objects=[
            Obj(
                id="door_1",
                type="door",
                visual_description="the green wooden door",
                center=[door_x, 5.0],
                size=[0.4, 1.4],
            )
        ],
        actors=[],
        camera=Camera(
            hfov_deg=60.0,
            track=[
                CamKey(t=0.0, x=0.0, y=0.0, yaw_deg=0.0),
                CamKey(t=3.0, x=0.0, y=0.0, yaw_deg=0.0),
            ],
        ),
    )


def test_a_abstain_zone_candidate_rejected(tmp_path: Path) -> None:
    """a) Moving the wrong object into the abstain margin (x=0 => bearing 0°) must be REJECTED."""
    truth = _make_door_only_scene(door_x=2.0)  # door is really on the RIGHT
    scene0 = _make_door_only_scene(door_x=-2.0)  # baseline mistakenly puts door on LEFT
    verify_fn, _ = _oracle_verifier(truth)

    def _dodge_to_abstain(prev: SceneV1, _f: list[CheckV1], _p: list[CheckV1]) -> SceneV1:
        cand = copy.deepcopy(prev)
        cand.objects[0].center = [0.0, 5.0]  # bearing 0° => inside ±6° SIDE abstain zone!
        cand.model_notes = "moved door_1 to center"
        return cand

    final, log = run_loop(
        scene0,
        tmp_path / "frames",
        tmp_path / "out",
        verify_fn=verify_fn,
        correct_fn=_dodge_to_abstain,
    )

    assert len(log) == 2  # round 0 + rejected round 1
    assert log[1]["accepted"] is False
    assert log[1]["score_cand"] <= log[1]["score_prev"]
    # Final kept scene0's door position and marked door_1 low_confidence
    assert final.entity("door_1").center[0] == -2.0  # type: ignore[union-attr]
    assert any(lc.entity_id == "door_1" for lc in final.low_confidence)


def test_b_deleting_wrong_object_rejected(tmp_path: Path) -> None:
    """b) Deleting the wrong object must be REJECTED (predict -> None is a miss)."""
    truth = _make_simple_scene(door_x=2.0)
    scene0 = _make_simple_scene(door_x=-2.0)
    verify_fn, _ = _oracle_verifier(truth)

    def _delete_door(prev: SceneV1, _f: list[CheckV1], _p: list[CheckV1]) -> SceneV1:
        cand = copy.deepcopy(prev)
        cand.objects = [o for o in cand.objects if o.id != "door_1"]
        cand.model_notes = "deleted door_1"
        return cand

    final, log = run_loop(
        scene0,
        tmp_path / "frames",
        tmp_path / "out",
        verify_fn=verify_fn,
        correct_fn=_delete_door,
    )

    assert log[1]["accepted"] is False
    assert log[1]["score_cand"] <= log[1]["score_prev"]
    assert any(o.id == "door_1" for o in final.objects)


def test_c_flipping_object_to_correct_side_accepted(tmp_path: Path) -> None:
    """c) Flipping the wrong object to the true side must be ACCEPTED, then stop on no fails."""
    truth = _make_simple_scene(door_x=2.0)
    scene0 = _make_simple_scene(door_x=-2.0)
    verify_fn, verified_calls = _oracle_verifier(truth)

    def _fix_door(prev: SceneV1, _f: list[CheckV1], _p: list[CheckV1]) -> SceneV1:
        cand = copy.deepcopy(prev)
        cand.objects[0].center = [2.0, 5.0]
        cand.model_notes = "door_1 @ 0.5–2.5: x -2.0 -> +2.0"
        return cand

    final, log = run_loop(
        scene0,
        tmp_path / "frames",
        tmp_path / "out",
        verify_fn=verify_fn,
        correct_fn=_fix_door,
    )

    assert log[1]["accepted"] is True
    assert log[1]["score_cand"] > log[1]["score_prev"]
    assert log[1]["score_cand"] == 1.0
    assert len(log) == 2  # stops before round 2 because no fails remain
    assert final.entity("door_1").center[0] == 2.0  # type: ignore[union-attr]
    assert final.low_confidence == []
    # Bank only grows, and NO qkey is verified twice
    assert log[1]["bank_size"] >= log[0]["bank_size"]
    assert len(verified_calls) == len(set(verified_calls))
    # Required artifacts written
    assert (tmp_path / "out" / "scene_r0.json").exists()
    assert (tmp_path / "out" / "scene_r1.json").exists()
    assert (tmp_path / "out" / "checks_r0.json").exists()
    assert (tmp_path / "out" / "checks_r1.json").exists()
    assert (tmp_path / "out" / "scene_final.json").exists()
    saved_log = json.loads((tmp_path / "out" / "loop_log.json").read_text(encoding="utf-8"))
    assert len(saved_log) == 2


def test_d_fixes_three_but_breaks_two_rejected(tmp_path: Path) -> None:
    """d) Candidate that fixes >=3 checks but breaks >=2 previously passing checks is REJECTED."""
    # Ground truth: door at +2.0, table at +2.0, lamp at -1.5
    truth = _make_simple_scene(door_x=2.0, table_x=2.0, lamp_x=-1.5)
    # Baseline: door wrong (-2.0), table right (+2.0), lamp right (-1.5)
    scene0 = _make_simple_scene(door_x=-2.0, table_x=2.0, lamp_x=-1.5)

    # Use custom checks so we control exact fix/regression counts:
    # 3 failing SIDE_RIGHT checks on door_1 + 2 passing SIDE_RIGHT checks on table_1
    def _controlled_checks(scene: SceneV1, fdir: Path) -> list[CheckV1]:
        all_c = make_checks(scene, fdir, sample_ts=[0.5, 1.5, 2.5])
        return [
            c
            for c in all_c
            if (c.entities == ["door_1"] and c.template in ("SIDE_LEFT", "SIDE_RIGHT") and not c.control)
            or (
                c.entities == ["table_1"]
                and c.template in ("SIDE_LEFT", "SIDE_RIGHT")
                and not c.control
                and c.t in (0.5, 1.5)
            )
        ]

    verify_fn, _ = _oracle_verifier(truth)

    def _fix_door_break_table(prev: SceneV1, _f: list[CheckV1], _p: list[CheckV1]) -> SceneV1:
        cand = copy.deepcopy(prev)
        cand.objects[0].center = [2.0, 5.0]  # fixes all 3 door checks
        cand.objects[1].center = [-2.0, 5.0]  # breaks both passing table checks (2 regressions!)
        return cand

    _, log = run_loop(
        scene0,
        tmp_path / "frames",
        tmp_path / "out",
        verify_fn=verify_fn,
        correct_fn=_fix_door_break_table,
        make_checks_fn=_controlled_checks,
    )

    assert log[1]["score_cand"] > log[1]["score_prev"]  # net score improved (3/5 vs 2/5)
    assert log[1]["regressions"]["count"] >= 2
    assert log[1]["accepted"] is False  # rejected because regressions > 1!


def test_freeze_identity_restores_ids_and_descriptions_without_undeleting() -> None:
    """Renamed ids and rewritten descriptions are restored; deleted entities stay deleted."""
    prev = _make_simple_scene()
    cand = copy.deepcopy(prev)
    # Rename id AND rewrite description of door_1 (same type + nearest center)
    cand.objects[0].id = "renamed_door"
    cand.objects[0].visual_description = "the emerald timber entryway"
    # Rewrite description of table_1 while keeping id
    cand.objects[1].visual_description = "a circular ivory table"
    # Delete lamp_1 (must stay deleted!)
    cand.objects = cand.objects[:2]
    # Rename actor id while keeping description
    cand.actors[0].id = "person_99"

    frozen = freeze_identity(cand, prev)

    obj_by_id = {o.id: o for o in frozen.objects}
    assert set(obj_by_id) == {"door_1", "table_1"}  # lamp_1 was NOT un-deleted
    assert obj_by_id["door_1"].visual_description == "the green wooden door"
    assert obj_by_id["table_1"].visual_description == "the round white dining table"
    assert frozen.actors[0].id == "a1"
    assert frozen.actors[0].visual_description == "the woman in the blue coat"


def test_stop_on_max_rounds_and_never_reverify_qkey(tmp_path: Path) -> None:
    """Loop stops at max_rounds, bank monotonically grows, and every qkey is verified at most once."""
    truth = _make_simple_scene(door_x=2.0, table_x=2.0, lamp_x=2.0)
    scene0 = _make_simple_scene(door_x=-2.0, table_x=-2.0, lamp_x=-2.0)
    verify_fn, verified_calls = _oracle_verifier(truth)
    step = {"i": 0}

    def _fix_one_per_round(prev: SceneV1, _f: list[CheckV1], _p: list[CheckV1]) -> SceneV1:
        cand = copy.deepcopy(prev)
        idx = step["i"]
        step["i"] += 1
        if idx < len(cand.objects):
            cand.objects[idx].center[0] = 2.0
            # Also deliberately rewrite id/description to prove freeze_identity runs inside loop
            cand.objects[idx].visual_description = f"mutated description {idx}"
        return cand

    final, log = run_loop(
        scene0,
        tmp_path / "frames",
        tmp_path / "out",
        verify_fn=verify_fn,
        correct_fn=_fix_one_per_round,
        max_rounds=2,
    )

    assert len(log) == 3  # round 0 + round 1 + round 2 (stops at max_rounds=2)
    assert log[1]["accepted"] is True
    assert log[2]["accepted"] is True
    assert log[0]["bank_size"] <= log[1]["bank_size"] <= log[2]["bank_size"]
    assert len(verified_calls) == len(set(verified_calls))
    # Original descriptions preserved across rounds
    assert {o.visual_description for o in final.objects} == {o.visual_description for o in scene0.objects}


def test_prompt_builder_and_mark_low_confidence() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    scene = SceneV1.load(repo_root / "mocks" / "scene_mock.json")
    checks = load_checks(repo_root / "mocks" / "checks_mock.json")
    failed = [c for c in checks if c.result and c.result.verdict == "fail"]
    passed = [c for c in checks if c.result and c.result.verdict == "pass"]

    prompt = build_correction_prompt(scene, failed, passed, ["Image 1 is at 00:03.000"])
    assert "FAILED checks" in prompt
    assert "PASSED checks" in prompt
    assert failed[0].check_id in prompt

    tagged = mark_low_confidence(scene, failed)
    assert len(tagged.low_confidence) == len(failed)
    assert {lc.entity_id for lc in tagged.low_confidence} == {"door_1", "a1"}


def test_transient_error_on_round2_preserves_round1(tmp_path: Path) -> None:
    """If round 2 raises a transient error (e.g. 503), round 1 is preserved and artifacts are saved."""
    truth = _make_simple_scene(door_x=2.0, table_x=2.0, lamp_x=2.0)
    scene0 = _make_simple_scene(door_x=-2.0, table_x=-2.0, lamp_x=-2.0)
    verify_fn, _ = _oracle_verifier(truth)
    calls = {"n": 0}

    def _fix_then_503(prev: SceneV1, _f: list[CheckV1], _p: list[CheckV1]) -> SceneV1:
        calls["n"] += 1
        if calls["n"] == 1:
            cand = copy.deepcopy(prev)
            cand.objects[0].center[0] = 2.0
            cand.model_notes = "fixed door_1"
            return cand
        raise RuntimeError("503 UNAVAILABLE")

    final, log = run_loop(
        scene0,
        tmp_path / "frames",
        tmp_path / "out",
        verify_fn=verify_fn,
        correct_fn=_fix_then_503,
        max_rounds=3,
    )

    assert log[1]["accepted"] is True
    assert log[2]["accepted"] is False
    assert "503" in log[2]["changes"]
    assert final.entity("door_1").center[0] == 2.0  # type: ignore[union-attr]
    assert (tmp_path / "out" / "scene_final.json").exists()
    assert (tmp_path / "out" / "loop_log.json").exists()
