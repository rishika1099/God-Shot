"""S11 scoring tests: geometry-based scoring of human labels, verifier-vs-human plumbing, run_all."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from eval import run_all
from eval import score as sc
from godshot import verify
from godshot.schema import CheckResult, LabelV1, Question, SceneV1

MAP = {
    "the man in the red hoodie": "a1",
    "the green door": "door_1",
    "the dining table": "table_1",
    "the window": "window_1",
}


def _labels(labels: list[dict[str, Any]], **kw: Any) -> LabelV1:
    base = {
        "clip_id": "mock_001",
        "source": "own_footage",
        "license": "consent_form_001",
        "split": "dev",
        "labeler": "test",
        "entity_map": MAP,
        "labels": labels,
    }
    return LabelV1.model_validate({**base, **kw})


# --------------------------------------------------------------------------- motion classes


@pytest.mark.parametrize(
    ("fwd", "lat", "dyaw", "want"),
    [
        (0.0, 0.0, 0.0, "static"),
        (0.5, 0.3, 30.0, "static"),  # small moves inside the 15% margin; pure pan is not a translation
        (2.0, 0.0, 0.0, "dolly_in"),
        (-2.0, 0.5, 0.0, "dolly_out"),
        (0.2, 2.0, 0.0, "truck_right"),
        (0.0, -2.0, 3.0, "truck_left"),
        (0.0, 2.0, 25.0, "arc_right"),  # moves right + turns left = orbit (S10 convention)
        (0.0, -2.0, -25.0, "arc_left"),  # moves left + turns right
        (0.0, 2.0, -25.0, "truck_right"),  # turning the "wrong" way is not an orbit
    ],
)
def test_classify_translation(fwd: float, lat: float, dyaw: float, want: str) -> None:
    m = {"forward": fwd, "lateral": lat, "dyaw": dyaw, "mean_scene_dist": 5.0}
    assert sc.classify_translation(m) == want


@pytest.mark.parametrize(("dyaw", "want"), [(40, "pan_left"), (-11, "pan_right"), (9.9, "none"), (0, "none")])
def test_classify_rotation(dyaw: float, want: str) -> None:
    m = {"forward": 0, "lateral": 0, "dyaw": dyaw, "mean_scene_dist": 5.0}
    assert sc.classify_rotation(m) == want


def test_empty_scene_floor_keeps_tiny_moves_static() -> None:
    m = {"forward": 0.1, "lateral": 0.0, "dyaw": 0.0, "mean_scene_dist": 0.0}
    assert sc.classify_translation(m) == "static"


# --------------------------------------------------------------------------- score_scene on the mock


def _by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["label_id"]: r for r in rows}


def test_score_scene_mock(scene_mock: SceneV1) -> None:
    rec = _labels(
        [
            {"label_id": "L1", "type": "CAM_TRANSLATION", "t0": 0, "t1": 4, "value": "dolly_in"},
            {"label_id": "L2", "type": "CAM_ROTATION", "t0": 4, "t1": 8, "value": "pan_left"},
            {"label_id": "L3", "type": "CAM_TRANSLATION", "t0": 8, "t1": 10, "value": "static"},
            {"label_id": "L4", "type": "CAM_ROTATION", "t0": 0, "t1": 4, "value": "none"},
            {
                "label_id": "L5",
                "type": "SIDE",
                "t": 1.0,
                "subject": "the man in the red hoodie",
                "value": "left",
            },
            {
                "label_id": "L6",
                "type": "SIDE",
                "t": 5.0,
                "subject": "the man in the red hoodie",
                "value": "right",
            },
            {
                "label_id": "L7",
                "type": "A_LEFT_OF_B",
                "t": 1.0,
                "a": "the man in the red hoodie",
                "b": "the dining table",
                "value": True,
            },
            {
                "label_id": "L8",
                "type": "CLOSER",
                "t": 2.0,
                "a": "the dining table",
                "b": "the window",
                "value": "a",
            },
            {
                "label_id": "L9",
                "type": "TOWARD",
                "t0": 0,
                "t1": 4,
                "subject": "the man in the red hoodie",
                "value": True,
            },
            {"label_id": "L10", "type": "SIDE", "t": 2.0, "subject": "the green door", "value": "left"},
            {"label_id": "L11", "type": "SIDE", "t": 1.0, "subject": "someone not mapped", "value": "left"},
            {"label_id": "L12", "type": "CAM_ROTATION", "t0": 4, "t1": 8, "value": "Pan_Right"},
        ]
    )
    r = _by_id(sc.score_scene(scene_mock, rec))
    for lid in ("L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9"):
        assert r[lid]["correct"], (lid, r[lid])
        assert not r[lid]["unresolved"]
    # door is to the camera's left at t=2 but outside the 60° FOV → the scene says it's off screen
    assert r["L10"]["pred"] == "offscreen" and not r["L10"]["correct"]
    assert r["L11"]["unresolved"] and not r["L11"]["correct"] and r["L11"]["pred"] is None
    assert r["L12"]["truth"] == "pan_right" and not r["L12"]["correct"]  # values are case-normalised
    assert set(r["L1"]) == {"label_id", "type", "pred", "truth", "correct", "unresolved"}


def test_score_changes_when_scene_is_corrected(scene_mock: SceneV1) -> None:
    rec = _labels(
        [
            {
                "label_id": "L1",
                "type": "SIDE",
                "t": 1.0,
                "subject": "the man in the red hoodie",
                "value": "right",
            }
        ]
    )
    assert not sc.score_scene(scene_mock, rec)[0]["correct"]
    fixed = scene_mock.model_copy(deep=True)
    for k in fixed.actors[0].track:
        k.x = -k.x  # mirror the actor across the camera axis
    assert sc.score_scene(fixed, rec)[0]["correct"]


def test_entity_map_pointing_at_missing_entity_is_unresolved(scene_mock: SceneV1) -> None:
    rec = _labels(
        [{"label_id": "L1", "type": "SIDE", "t": 1.0, "subject": "ghost", "value": "left"}],
        entity_map={"ghost": "a9"},
    )
    assert sc.score_scene(scene_mock, rec)[0]["unresolved"]


def test_hidden_actor_is_offscreen(scene_mock: SceneV1) -> None:
    hidden = scene_mock.model_copy(deep=True)
    for k in hidden.actors[0].track:
        k.visible = False
    rec = _labels(
        [
            {
                "label_id": "L1",
                "type": "SIDE",
                "t": 1.0,
                "subject": "the man in the red hoodie",
                "value": "left",
            }
        ]
    )
    assert sc.score_scene(hidden, rec)[0]["pred"] == "offscreen"


def test_camerabench_style_whole_clip_label(scene_mock: SceneV1) -> None:
    rec = _labels(
        [{"label_id": "L1", "type": "CAM_TRANSLATION", "t0": 0, "t1": 10, "value": "dolly_in"}],
        source="camerabench",
        entity_map={},
    )
    assert sc.score_scene(scene_mock, rec)[0]["correct"]


# --------------------------------------------------------------------------- askable / verifier vs human


def test_askable_mapping() -> None:
    rec = _labels(
        [
            {"label_id": "a", "type": "SIDE", "t": 1, "subject": "x", "value": "left"},
            {"label_id": "b", "type": "CLOSER", "t": 1, "a": "x", "b": "y", "value": "b"},
            {"label_id": "c", "type": "CAM_ROTATION", "t0": 0, "t1": 2, "value": "none"},
            {"label_id": "d", "type": "CAM_TRANSLATION", "t0": 0, "t1": 2, "value": "arc_left"},
            {"label_id": "e", "type": "CAM_TRANSLATION", "t0": 0, "t1": 2, "value": "dolly_out"},
            {"label_id": "f", "type": "TOWARD", "t0": 0, "t1": 2, "subject": "x", "value": False},
        ]
    )
    got = {lb.label_id: sc.askable(lb) for lb in rec.labels}
    assert got["a"] == ("SIDE_LEFT", ["x"], 1, None, True)
    assert got["b"] == ("CLOSER", ["x", "y"], 1, None, "b_second")
    assert got["c"] is None and got["d"] is None
    assert got["e"] == ("DOLLY", [], 0, 2, "dolly_out")
    assert got["f"] == ("TOWARD", ["x"], 0, 2, False)


def _fake_observed(raw: CheckResult) -> Any:
    if raw.p_yes is not None:
        return True if raw.p_yes >= 0.7 else False if raw.p_yes <= 0.3 else None
    top = max(raw.choice_probs, key=raw.choice_probs.get)
    return top if top != "other" and raw.choice_probs[top] >= 0.5 else None


def test_verifier_vs_human_rows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(verify, "observed", _fake_observed, raising=False)
    rec = _labels(
        [
            {"label_id": "L1", "type": "SIDE", "t": 1, "subject": "x", "value": "left"},
            {"label_id": "L2", "type": "CAM_TRANSLATION", "t0": 0, "t1": 30, "value": "dolly_in"},
            {"label_id": "L3", "type": "A_LEFT_OF_B", "t": 2, "a": "x", "b": "y", "value": False},
            {"label_id": "L4", "type": "CAM_TRANSLATION", "t0": 0, "t1": 5, "value": "static"},  # not askable
        ]
    )
    asked: list[tuple] = []

    def build(template: str, descs: list[str], t: float, t_end: float | None) -> Question:
        if template == "DOLLY":
            return Question(
                type="choice",
                instructions="Did it move?",
                options={"dolly_in": "in", "dolly_out": "out", "other": "neither"},
            )
        return Question(type="noul", instructions=f"{template} {descs}?")

    def frames_at(clip: Path, ts: list[float], out_dir: Path) -> list[Path]:
        asked.append((clip.name, tuple(ts), out_dir.name))
        return [out_dir / f"{round(t * 1000):06d}.jpg" for t in ts]

    def ask(q: Question, images: list[Path], context: str) -> CheckResult:
        if q.type == "choice":
            return CheckResult(choice_probs={"dolly_in": 0.8, "dolly_out": 0.1, "other": 0.1}, confidence=0.8)
        return CheckResult(p_yes=0.9 if q.instructions.startswith("SIDE") else 0.5, confidence=0.9)

    clips = {"mock_001": {"id": "mock_001", "path": "eval/clips/mock_001.mp4", "duration_s": "10.0"}}
    rows = {r["label_id"]: r for r in sc.verifier_vs_human([rec], clips, tmp_path, build, ask, frames_at)}

    assert set(rows) == {"L1", "L2", "L3"}
    assert rows["L1"]["correct"] and rows["L1"]["truth_bool"] is True and rows["L1"]["p_yes"] == 0.9
    assert rows["L2"]["correct"] and rows["L2"]["truth_bool"] is None and rows["L2"]["observed"] == "dolly_in"
    assert rows["L3"]["uncertain"] and not rows["L3"]["correct"]
    # t1=30 s is clamped inside the 10 s clip before extracting frames
    assert ("mock_001.mp4", (0.0, 9.95), "mock_001") in asked
    json.dumps(list(rows.values()))  # serialisable for verifier_vs_human_<split>.json


def test_verifier_vs_human_skips_unknown_clip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rec = _labels([{"label_id": "L1", "type": "SIDE", "t": 1, "subject": "x", "value": "left"}])
    rows = sc.verifier_vs_human([rec], {}, tmp_path, lambda *a: None, lambda *a: None, lambda *a: [])
    assert rows == []


# --------------------------------------------------------------------------- summarize_runs


def test_summarize_runs(tmp_path: Path, scene_mock: SceneV1) -> None:
    rec = _labels(
        [
            {
                "label_id": "L1",
                "type": "SIDE",
                "t": 1.0,
                "subject": "the man in the red hoodie",
                "value": "right",
            }
        ]
    )
    wrong = scene_mock
    right = scene_mock.model_copy(deep=True)
    for k in right.actors[0].track:
        k.x = -k.x
    wrong.save(tmp_path / "baseline" / "mock_001" / "scene_r0.json")
    right.save(tmp_path / "full" / "mock_001" / "scene_final.json")
    s = sc.summarize_runs([rec], tmp_path)
    assert s["baseline"]["accuracy"] == 0.0 and s["full"]["accuracy"] == 1.0
    assert s["full"]["clips"] == 1 and s["full"]["by_type"] == {"SIDE": 1.0}


# --------------------------------------------------------------------------- run_all


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["id", "source", "license", "split", "difficulty", "duration_s", "path"]
        )
        w.writeheader()
        for r in rows:
            w.writerow(
                {"source": "own_footage", "license": "x", "difficulty": "easy", "duration_s": "5", **r}
            )


def test_run_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clip_ok = tmp_path / "ok.mp4"
    clip_bad = tmp_path / "bad.mp4"
    clip_ok.write_bytes(b"x")
    clip_bad.write_bytes(b"x")
    csv_path = tmp_path / "clips.csv"
    _write_csv(
        csv_path,
        [
            {"id": "ok", "split": "dev", "path": str(clip_ok)},
            {"id": "bad", "split": "dev", "path": str(clip_bad)},
            {"id": "gone", "split": "dev", "path": str(tmp_path / "gone.mp4")},
            {"id": "other", "split": "test", "path": str(clip_ok)},
        ],
    )
    monkeypatch.setattr(run_all, "CLIPS_CSV", csv_path)
    calls: list[str] = []

    def fake_full(clip: Path, out_dir: Path) -> None:
        calls.append(clip.name)
        if clip.name == "bad.mp4":
            raise RuntimeError("boom")
        (out_dir / "scene_final.json").write_text("{}", encoding="utf-8")

    monkeypatch.setitem(run_all.RUNNERS, "full", fake_full)
    runs = tmp_path / "runs"
    res = {r["clip_id"]: r for r in run_all.main("dev", "full", runs_dir=runs)}
    assert res["ok"]["status"] == "ok"
    assert res["bad"]["status"] == "error" and "boom" in res["bad"]["error"]
    assert res["gone"]["status"] == "missing"
    assert "other" not in res
    assert (runs / "full" / "_run_dev.json").exists()

    again = {r["clip_id"]: r for r in run_all.main("dev", "full", runs_dir=runs)}
    assert again["ok"]["status"] == "skipped"
    assert sorted(calls) == ["bad.mp4", "bad.mp4", "ok.mp4"]
