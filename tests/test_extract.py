"""S02 tests. Fully offline: the Argon response comes from tests/fixtures/extract_response.json."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from godshot import cache, extract
from godshot.schema import CamKey, Clip, SceneV1

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "extract_response.json"
CLIP = Clip(id="dev01", duration_s=10.0, fps=24.0, width=1280, height=720)


@pytest.fixture
def payload() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def clip_file(tmp_path: Path) -> Path:
    p = tmp_path / "dev01.mp4"
    p.write_bytes(b"not really a video")
    return p


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Isolated cache + fake probe/upload/generate. `state['responses']` feeds _generate in order."""
    state: dict[str, Any] = {"generate": 0, "upload": 0, "responses": []}
    monkeypatch.setattr(cache, "_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(extract, "_probe", lambda clip: CLIP)

    def fake_upload(clip: Path) -> str:
        state["upload"] += 1
        return "files/fake"

    def fake_generate(model: str, contents: list[Any]) -> dict[str, Any]:
        state["generate"] += 1
        return state["responses"].pop(0)

    monkeypatch.setattr(extract, "_upload", fake_upload)
    monkeypatch.setattr(extract, "_generate", fake_generate)
    monkeypatch.setattr(extract, "detect_cuts", lambda clip: state.get("cuts", []))
    return state


# --------------------------------------------------------------------------- end to end (offline)


def test_baseline_writes_valid_scene_and_log(api, payload, clip_file, tmp_path):
    api["responses"] = [payload]
    out = tmp_path / "out" / "dev01"
    scene = extract.baseline(clip_file, out)

    loaded = SceneV1.load(out / "scene_r0.json")
    assert loaded == scene
    assert scene.clip == CLIP  # probed metadata wins over the model's guess
    log = json.loads((out / "loop_log.json").read_text())
    assert log[0]["round"] == 0
    assert log[0]["tokens"]["total_token_count"] == 7100
    assert log[0]["latency_s"] == pytest.approx(21.4)
    assert log[0]["latency_s_per_10s"] == pytest.approx(21.4)
    assert log[0]["cached_calls"] == 0


def test_second_run_makes_no_network_call(api, payload, clip_file, monkeypatch):
    api["responses"] = [payload]
    first = extract.extract(clip_file)
    monkeypatch.setenv("GODSHOT_OFFLINE", "1")  # any miss would raise CacheMiss
    second, stats = extract.extract_with_stats(clip_file)
    assert second == first
    assert api["generate"] == 1 and api["upload"] == 1
    assert stats.cached_calls == stats.calls == 1


def test_repair_call_on_invalid_json(api, payload, clip_file):
    api["responses"] = [{"text": "{not json", "usage": {}, "latency_s": 1.0}, payload]
    scene, stats = extract.extract_with_stats(clip_file)
    assert isinstance(scene, SceneV1)
    assert stats.calls == 2 and api["generate"] == 2


def test_raises_after_failed_repair(api, clip_file):
    bad = {"text": json.dumps({"camera": {"track": []}}), "usage": {}, "latency_s": 1.0}
    api["responses"] = [bad, bad]
    with pytest.raises(extract.ExtractError):
        extract.extract(clip_file)


def test_loop_log_upserts_round0(tmp_path):
    path = tmp_path / "loop_log.json"
    path.write_text(json.dumps([{"round": 1, "pass_rate": 0.8}, {"round": 0, "old": True}]))
    extract.write_loop_log(path, {"round": 0, "new": True})
    rounds = json.loads(path.read_text())
    assert [r["round"] for r in rounds] == [0, 1] and rounds[0] == {"round": 0, "new": True}


# --------------------------------------------------------------------------- post-processing (pure)


@pytest.fixture
def scene(payload) -> SceneV1:
    return extract.parse_scene(payload["text"], CLIP)


def test_keyframes_sorted_with_endpoints_and_mmss(scene):
    for track in [scene.camera.track, *(a.track for a in scene.actors)]:
        ts = [k.t for k in track]
        assert ts == sorted(ts)
        assert ts[0] == 0.0 and ts[-1] == CLIP.duration_s
        assert all(k.mmss == f"00:{int(k.t):02d}.{round(k.t % 1 * 1000):03d}" for k in track)
    actor = scene.actors[0].track
    assert (actor[0].x, actor[0].y) == (0.0, 7.0)  # t=0 holds the first real key (t=0.5)
    assert actor[1].mmss == "00:00.500"


def test_coords_clamped_to_room_bbox(scene):
    lamp = scene.entity("lamp_1")
    assert lamp.center == [5.0, 7.0]  # x=20 → 4 + 1
    assert min(k.y for k in scene.camera.track) == -1.0  # y=-50 → 0 - 1


def test_yaw_spike_replaced_with_midpoint(scene):
    yaw = {k.t: k.yaw_deg for k in scene.camera.track}
    assert yaw[2.0] == pytest.approx(7.5)  # spike 170° between 5° and 10°
    assert "yaw jump at 00:02.000" in scene.model_notes
    assert scene.model_notes.startswith("door partially occluded")


def test_yaw_jump_without_return_halves_it():
    track = [{"t": 0, "x": 0, "y": 0, "yaw_deg": 0}, {"t": 0.5, "x": 0, "y": 0, "yaw_deg": 160}]
    s = SceneV1.model_validate({"clip": CLIP.model_dump(), "camera": {"track": track}})
    out = extract.fix_yaw_jumps(s)
    assert out.camera.track[1].yaw_deg == pytest.approx(80.0)
    assert s.camera.track[1].yaw_deg == 160  # pure: input untouched


def test_slow_large_turn_is_kept():
    track = [CamKey(t=0, x=0, y=0, yaw_deg=0), CamKey(t=2.0, x=0, y=0, yaw_deg=150)]
    s = SceneV1(clip=CLIP, camera={"track": track})
    assert extract.fix_yaw_jumps(s).camera.track[1].yaw_deg == 150


def test_yaw_wraparound_is_not_a_jump():
    track = [CamKey(t=0, x=0, y=0, yaw_deg=170), CamKey(t=0.5, x=0, y=0, yaw_deg=-170)]
    s = SceneV1(clip=CLIP, camera={"track": track})
    assert extract.fix_yaw_jumps(s).camera.track[1].yaw_deg == -170


def test_duplicate_descriptions_get_a_distinguishing_detail(scene):
    descs = [e.visual_description for e in [*scene.objects, *scene.actors]]
    assert len({d.lower() for d in descs}) == len(descs)
    assert scene.entity("chair_2").visual_description == "The red chair (the one further right)"


def test_duplicate_at_same_spot_is_dropped():
    raw = {"objects": [
        {"id": "a", "visual_description": "the sofa", "center": [1, 1]},
        {"id": "b", "visual_description": "the sofa", "center": [1.1, 1]},
    ]}
    out, notes = extract.dedupe_descriptions(raw)
    assert [o["id"] for o in out["objects"]] == ["a"] and "dropped" in notes[0]


def test_sanitize_caps_and_unique_ids():
    objs = [{"id": "o", "visual_description": f"thing {i}", "center": [0, i], "confidence": i / 10}
            for i in range(8)]
    raw = extract.sanitize_raw({"objects": objs, "camera": {"track": [{"t": 0, "x": 0, "y": 0}]}}, CLIP)
    assert len(raw["objects"]) == 6
    assert len({o["id"] for o in raw["objects"]}) == 6
    assert "kept the 6 most confident of 8 objects" in raw["model_notes"]


def test_no_room_means_no_clamp():
    s = SceneV1(clip=CLIP, camera={"track": [CamKey(t=0, x=50, y=50)]})
    assert extract.clamp_coords(s).camera.track[0].x == 50


def test_prompt_file_has_coordinate_rules():
    text = extract.load_prompt()
    assert "Origin = camera position at 00:00.000" in text and "yaw_deg" in text


def test_cuts_flow_into_scene(api, payload, clip_file):
    api["responses"], api["cuts"] = [payload], [4.0]
    scene = extract.extract(clip_file)
    assert [(lc.entity_id, lc.t0, lc.t1) for lc in scene.low_confidence] == [("camera", 3.5, 4.5)]
    assert "1 hard cut(s) detected at 00:04.000" in scene.model_notes


# --------------------------------------------------------------------------- motion segments from the track


def _m(fwd=0.0, lat=0.0, dyaw=0.0, dist=3.0) -> dict[str, float]:
    return {"forward": fwd, "lateral": lat, "dyaw": dyaw, "mean_scene_dist": dist}


@pytest.mark.parametrize(
    ("motion", "expected"),
    [
        (_m(), ("static", "none")),
        (_m(fwd=0.5), ("dolly_in", "none")),
        (_m(fwd=-0.5), ("dolly_out", "none")),
        (_m(lat=0.5), ("truck_right", "none")),
        (_m(lat=-0.5), ("truck_left", "none")),
        (_m(dyaw=20), ("static", "pan_left")),
        (_m(dyaw=-20), ("static", "pan_right")),
        (_m(lat=0.5, dyaw=20), ("arc_right", "pan_left")),  # moving right, turning left: circling
        (_m(lat=-0.5, dyaw=-20), ("arc_left", "pan_right")),
        (_m(lat=0.5, dyaw=-20), ("truck_right", "pan_right")),  # turning away from the subject: not an arc
        (_m(fwd=0.05, dyaw=3), ("static", "none")),  # below both thresholds
        (_m(fwd=0.2, dist=10.0), ("static", "none")),  # 0.2 < 5% of a 10-unit scene distance
    ],
)
def test_classify_motion(motion, expected):
    assert extract.classify_motion(motion, 1.0) == expected


def test_derived_segments_match_mock(scene_mock):
    s = extract.derive_motion_segments(scene_mock)
    got = [(m.t0, m.t1, m.translation, m.rotation) for m in s.camera.motion_segments]
    assert got == [(0, 4, "dolly_in", "none"), (4, 8, "static", "pan_left"), (8, 10, "static", "none")]
    assert "derived from the camera track" not in s.model_notes  # mock labels already agree


def test_contradicting_labels_replaced_and_noted(scene_mock):
    wrong = scene_mock.model_copy(deep=True)
    for m in wrong.camera.motion_segments:
        m.translation, m.rotation = "dolly_out", "pan_right"
    s = extract.derive_motion_segments(wrong)
    assert s.camera.motion_segments[0].translation == "dolly_in"
    assert "model said: 0.0-4.0s dolly_out/pan_right" in s.model_notes


def test_orbit_track_is_an_arc():
    # camera circles a subject at (0, 2), radius 2, moving to its right and always facing the centre
    keys = []
    for i in range(9):
        a = math.radians(-90 + 10 * i)  # start below the subject, go counterclockwise
        x, y = 2 * math.cos(a), 2 + 2 * math.sin(a)
        keys.append(CamKey(t=i * 0.5, x=x, y=y, yaw_deg=10 * i))  # yaw follows the angle: facing centre
    s = SceneV1(clip=CLIP.model_copy(update={"duration_s": 4.0}), camera={"track": keys},
                actors=[{"id": "a", "visual_description": "subject", "track": [{"t": 0, "x": 0, "y": 2}]}])
    segs = extract.derive_motion_segments(s).camera.motion_segments
    assert [(m.translation, m.rotation) for m in segs] == [("arc_right", "pan_left")]


def test_windows_merge_short_tail():
    assert extract._windows(3.1) == [(0, 1), (1, 2), (2, 3.1)]
    assert extract._windows(3.5) == [(0, 1), (1, 2), (2, 3), (3, 3.5)]


# --------------------------------------------------------------------------- cuts


def test_mark_cuts_ignores_edges_and_is_pure(scene_mock):
    s = extract.mark_cuts(scene_mock, [0.04, 5.0, 9.95])
    assert [(lc.t0, lc.t1, lc.failed_checks) for lc in s.low_confidence] == [(4.5, 5.5, ["cut_detected"])]
    assert scene_mock.low_confidence == []
    assert extract.mark_cuts(scene_mock, []) is scene_mock


needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="no ffmpeg")


def _synth(out: Path, lavfi: str) -> Path:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", lavfi, "-pix_fmt", "yuv420p",
                    str(out)], check=True)
    return out


@needs_ffmpeg
def test_detect_cuts_finds_hard_cut(tmp_path: Path):
    red, blue = "color=c=red:s=320x240:d=1:r=24", "color=c=blue:s=320x240:d=1:r=24"
    red_then_blue = f"{red}[a];{blue}[b];[a][b]concat=n=2:v=1"
    clip = _synth(tmp_path / "cut.mp4", red_then_blue)
    cuts = extract.detect_cuts(clip)
    assert len(cuts) == 1 and cuts[0] == pytest.approx(1.0, abs=0.05)


@needs_ffmpeg
def test_detect_cuts_none_in_continuous_shot(tmp_path: Path):
    assert extract.detect_cuts(_synth(tmp_path / "cont.mp4", "testsrc2=s=320x240:d=2:r=24")) == []
