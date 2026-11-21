"""S00: contracts validate, mocks are valid, round-trips are lossless, cache works offline."""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import pytest
from pydantic import ValidationError

from godshot import cache
from godshot.config import load_config
from godshot.schema import (
    CheckResult,
    CheckV1,
    LabelV1,
    Question,
    SceneV1,
    load_checks,
    load_labels,
    make_check_id,
    mmss,
    qkey,
    save_checks,
    save_labels,
)


@pytest.mark.parametrize(
    ("t", "want"),
    [(0, "00:00.000"), (2.0, "00:02.000"), (62.5, "01:02.500"), (9.6, "00:09.600"), (-1, "00:00.000")],
)
def test_mmss(t: float, want: str) -> None:
    assert mmss(t) == want


# ---------------------------------------------------------------- scene.v1


def test_scene_mock_valid(scene_mock: SceneV1) -> None:
    assert scene_mock.schema_ == "scene.v1"
    assert scene_mock.clip.duration_s == 10.0
    assert {o.type for o in scene_mock.objects} == {"door", "table", "window"}
    assert len(scene_mock.actors) == 1
    segs = [(s.t0, s.t1, s.translation, s.rotation) for s in scene_mock.camera.motion_segments]
    assert segs[0] == (0.0, 4.0, "dolly_in", "none")
    assert segs[1] == (4.0, 8.0, "static", "pan_left")
    # actor walks left → right
    xs = [k.x for k in scene_mock.actors[0].track]
    assert xs == sorted(xs) and xs[0] < 0 < xs[-1]
    # keyframes at least every 1 s
    for track in (scene_mock.camera.track, scene_mock.actors[0].track):
        ts = [k.t for k in track]
        assert all(b - a <= 1.0 for a, b in pairwise(ts))


def test_scene_roundtrip_lossless(scene_mock: SceneV1, scene_mock_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "scene.json"
    scene_mock.save(out)
    again = SceneV1.load(out)
    assert again == scene_mock
    assert json.loads(out.read_text()) == json.loads(scene_mock_path.read_text())
    assert json.loads(out.read_text())["schema"] == "scene.v1"  # alias, not schema_


def test_scene_rejects_bad_enum_and_duplicates(scene_mock: SceneV1) -> None:
    data = scene_mock.model_dump(by_alias=True)
    bad = json.loads(json.dumps(data))
    bad["camera"]["motion_segments"][0]["translation"] = "zoom_in"
    with pytest.raises(ValidationError):
        SceneV1.model_validate(bad)
    dup = json.loads(json.dumps(data))
    dup["objects"][1]["id"] = dup["objects"][0]["id"]
    with pytest.raises(ValidationError):
        SceneV1.model_validate(dup)


def test_mmss_filled_when_missing(scene_mock: SceneV1) -> None:
    data = scene_mock.model_dump(by_alias=True)
    for k in data["camera"]["track"]:
        k["mmss"] = ""
    s = SceneV1.model_validate(data)
    assert s.camera.track[3].mmss == "00:03.000"


def test_scene_json_schema_has_no_prefix_items() -> None:
    # Gemini structured output: keep the schema simple (no tuple prefixItems).
    assert "prefixItems" not in json.dumps(SceneV1.model_json_schema(by_alias=True))


# ---------------------------------------------------------------- check.v1


def test_checks_mock_valid(checks_mock: list[CheckV1], scene_mock: SceneV1) -> None:
    assert 8 <= len(checks_mock) <= 15
    assert sum(c.result is not None and c.result.verdict == "fail" for c in checks_mock) == 2
    assert all(c.result is not None for c in checks_mock)
    ids = {e.id for e in [*scene_mock.objects, *scene_mock.actors]}
    for c in checks_mock:
        assert set(c.entities) <= ids
        for eid in c.entities:  # Decisions never sees ids: instructions must not mention them
            assert eid not in c.question.instructions


def test_checks_roundtrip_lossless(
    checks_mock: list[CheckV1], checks_mock_path: Path, tmp_path: Path
) -> None:
    out = tmp_path / "checks.json"
    save_checks(checks_mock, out)
    assert load_checks(out) == checks_mock
    assert json.loads(out.read_text()) == json.loads(checks_mock_path.read_text())


def test_question_to_api_wire_format(checks_mock: list[CheckV1]) -> None:
    by_type = {c.question.type: c.question for c in checks_mock}
    noul = by_type["noul"].to_api()
    assert noul["type"] == "noul" and set(noul["criteria"]) == {"true_description", "false_description"}
    ch = by_type["choice"].to_api()
    assert ch["type"] == "choice" and "other" in ch["criteria"] and "options" not in ch


def test_check_validation() -> None:
    with pytest.raises(ValidationError):  # pair needs 2 paths
        CheckV1.model_validate(
            {
                "check_id": "x",
                "template": "PAN",
                "t": 0,
                "input_ref": {"kind": "pair", "paths": ["a.jpg"]},
                "expected": "pan_left",
                "question": {
                    "type": "choice",
                    "instructions": "How?",
                    "options": {"pan_left": "L", "other": "O"},
                },
            }
        )
    with pytest.raises(ValidationError):  # choice needs options
        Question(type="choice", instructions="Which?")


def test_qkey_and_check_ids(checks_mock: list[CheckV1]) -> None:
    keys = [qkey(c) for c in checks_mock]
    assert len(set(keys)) == len(keys)
    assert all(isinstance(k[0], tuple) and isinstance(k[1], str) for k in keys)  # hashable
    side_l, side_r = (c for c in checks_mock if c.template.startswith("SIDE") and c.t == 1.0)
    assert qkey(side_l) != qkey(side_r) and side_l.check_id != side_r.check_id
    for c in checks_mock:
        assert c.check_id == make_check_id(c.template, c.entities, c.t)
    assert make_check_id("SIDE_LEFT", ["a1"], 1.0) == make_check_id("SIDE_LEFT", ["a1"], 1.0000001)


def test_raw_result_without_verdict() -> None:
    raw = CheckResult(p_yes=0.83, confidence=0.83)
    assert raw.verdict is None and raw.choice_probs is None


# ---------------------------------------------------------------- label.v1

LABEL = {
    "clip_id": "own_007",
    "source": "own_footage",
    "license": "consent_form_007",
    "split": "dev",
    "difficulty": ["handheld"],
    "labeler": "Rishika",
    "entity_map": {"the man in the blue hoodie": "a1"},
    "labels": [
        {"label_id": "L1", "type": "CAM_TRANSLATION", "t0": 0, "t1": 4, "value": "dolly_in"},
        {"label_id": "L2", "type": "CAM_ROTATION", "t0": 4, "t1": 7, "value": "pan_right"},
        {
            "label_id": "L3",
            "type": "SIDE",
            "t": 3.0,
            "subject": "the man in the blue hoodie",
            "value": "left",
        },
        {"label_id": "L4", "type": "A_LEFT_OF_B", "t": 3.0, "a": "the man", "b": "the door", "value": True},
        {"label_id": "L5", "type": "CLOSER", "t": 6.0, "a": "the man", "b": "the door", "value": "a"},
        {"label_id": "L6", "type": "TOWARD", "t0": 2, "t1": 5, "subject": "the man", "value": True},
    ],
}


def test_label_roundtrip(tmp_path: Path) -> None:
    lb = LabelV1.model_validate(LABEL)
    assert lb.labels[3].value is True and lb.labels[4].value == "a"
    out = tmp_path / "labels.jsonl"
    save_labels([lb, lb], out)
    assert load_labels(out) == [lb, lb]


def test_label_requires_times() -> None:
    bad = json.loads(json.dumps(LABEL))
    del bad["labels"][0]["t1"]
    with pytest.raises(ValidationError):
        LabelV1.model_validate(bad)


# ---------------------------------------------------------------- cache


@pytest.fixture
def tmp_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GODSHOT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GODSHOT_OFFLINE", raising=False)
    load_config.cache_clear()
    yield tmp_path / "cache"
    load_config.cache_clear()


def test_cache_hit_and_offline(tmp_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fn() -> dict:
        calls.append(1)
        return {"answer": (1, 2)}

    key = [b"\x00clip", "model", "prompt"]
    assert cache.cached(key, fn, "test") == {"answer": [1, 2]}
    assert cache.cached(key, fn, "test") == {"answer": [1, 2]}
    assert len(calls) == 1
    assert len(list((tmp_cache / "test").glob("*.json"))) == 1

    monkeypatch.setenv("GODSHOT_OFFLINE", "1")
    assert cache.cached(key, fn, "test") == {"answer": [1, 2]}  # hit still works offline
    with pytest.raises(cache.CacheMiss):
        cache.cached([b"other"], fn, "test")


def test_cache_bytes_and_key_separation(tmp_cache: Path) -> None:
    assert cache.cached(["v"], lambda: b"\x89PNG", "img") == b"\x89PNG"
    assert cache.cached(["v"], lambda: b"changed", "img") == b"\x89PNG"
    assert cache.cache_key(["ab", "c"]) != cache.cache_key(["a", "bc"])
