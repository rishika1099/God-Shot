"""S14 tests: scene diff, prompt assembly, caching, preservation, and the opt-in S12 hook. Offline."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from godshot import cache, direct, reshoot
from godshot.schema import CheckResult, SceneV1

MOCK = Path(__file__).resolve().parent.parent / "mocks" / "scene_mock.json"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="no ffmpeg")

BRIEF = direct.SceneBrief(
    logline="A woman in a red coat crosses the room to the window.",
    intent="Her slow walk shows hesitation before she looks outside.",
    beats=[direct.Beat(t0=0, t1=6, who="the woman in the red coat", action="walks to the window",
                       detail="eyes down, hands in pockets")],
    performances=[direct.Performance(who="the woman in the red coat", gait="slow walk", pace="0.6 m/s",
                                     posture="shoulders hunched", emotion="hesitant",
                                     wardrobe="long red wool coat, black boots")],
    environment="cold blue daylight from the window, dust in the air",
    physics=["the coat swings with each step"],
    must_preserve=[
        direct.PreserveItem(element="the red coat", why="identity", check="Is a woman wearing a red coat?"),
        direct.PreserveItem(element="the window", why="goal", check="Is there a window with daylight?"),
    ],
)
DIRECTION = direct.Direction(
    change_summary="Her path is twice as long in the same time.",
    adaptations=[direct.Adaptation(who="the woman in the red coat", change="path 2× longer",
                                   reasoning="1.2 m/s is a brisk walk", behavior="brisk but heavy steps")],
    preserved=["the red coat: unchanged"],
    performance_prompt="The woman in the red coat walks briskly but heavily to the window, eyes down.",
)


@pytest.fixture
def scene() -> SceneV1:
    return SceneV1.load(MOCK)


def _stretched(scene: SceneV1, k: float = 2.0) -> SceneV1:
    """Edited copy: the first actor's path is k× longer (same timing), the first object moved 1 m."""
    s = scene.model_copy(deep=True)
    a = s.actors[0]
    x0, y0 = a.track[0].x, a.track[0].y
    for key in a.track:
        key.x, key.y = x0 + (key.x - x0) * k, y0 + (key.y - y0) * k
    s.objects[0].center = [s.objects[0].center[0] + 1.0, s.objects[0].center[1]]
    return s


# --------------------------------------------------------------------------- pure geometry


@pytest.mark.parametrize(
    ("v", "g"), [(0.1, "standing / shifting weight"), (1.2, "walk"), (2.5, "jog"), (4, "run")]
)
def test_gait(v, g):
    assert direct.gait(v) == g


def test_actor_motion_speed(scene):
    a = scene.actors[0].model_copy(deep=True)
    for key in a.track:
        key.x, key.y, key.visible = 0.0, 1.5 * key.t, True  # straight line, 1.5 m/s
    m = direct.actor_motion(a, 4.0)
    assert m["path_m"] == pytest.approx(6.0, abs=0.05) and m["speed_mps"] == pytest.approx(1.5, abs=0.02)
    assert m["gait"] == "walk" and m["moving_speed_mps"] == pytest.approx(1.5, abs=0.02)


def test_moving_speed_ignores_standing(scene):
    a = scene.actors[0].model_copy(deep=True)
    for key in a.track:  # stands for 2 s, then walks 1.5 m/s
        key.x, key.y, key.visible = 0.0, 1.5 * max(0.0, key.t - 2.0), True
    m = direct.actor_motion(a, 4.0)
    assert m["speed_mps"] == pytest.approx(0.75, abs=0.05)
    assert m["moving_speed_mps"] == pytest.approx(1.5, abs=0.05)
    assert m["moving_s"] == pytest.approx(2.0, abs=0.1)


def test_scene_diff_reports_changes_without_ids(scene):
    edited = _stretched(scene)
    d = direct.scene_diff(scene, edited)
    person = d["people"][0]
    assert person["status"] == "changed" and person["who"] == scene.actors[0].visual_description
    assert person["speed_ratio"] == pytest.approx(2.0, abs=0.1)
    assert d["objects"][0]["status"] == "moved" and d["objects"][0]["moved_m"] == pytest.approx(1.0)
    assert d["camera"]["before"] == d["camera"]["after"]  # camera untouched
    text = json.dumps(d)
    for e in [*scene.actors, *scene.objects]:
        assert f'"{e.id}"' not in text  # agents see descriptions, never ids


def test_scene_diff_added_and_removed(scene):
    edited = scene.model_copy(deep=True)
    gone = edited.actors.pop(0)
    d = direct.scene_diff(scene, edited)
    assert {"who": gone.visual_description, "status": "removed"} in d["people"]
    assert direct.scene_diff(edited, scene)["people"][0]["status"] == "added"


def test_unchanged_scene_diff(scene):
    d = direct.scene_diff(scene, scene.model_copy(deep=True))
    assert all(p["status"] == "unchanged" for p in d["people"]) and d["objects"] == []


def test_map_summary_has_no_ids(scene):
    text = direct._map_summary(scene)
    assert all(f'"{e.id}"' not in text for e in [*scene.actors, *scene.objects])
    assert scene.actors[0].visual_description in text


# --------------------------------------------------------------------------- prompt + ranking


def test_directed_prompt_keeps_geometry_first_and_verbatim():
    base = "Re-render this previs.\n00:00–00:04: the camera dollies in about 2 m."
    p = direct.directed_prompt(base, BRIEF, DIRECTION)
    assert p.startswith(base)
    assert direct.CONFLICT_RULE in p and DIRECTION.performance_prompt in p
    assert "MUST KEEP: the red coat; the window." in p
    assert "long red wool coat" in p and "cold blue daylight" in p


def test_reshoot_directed_is_opt_in():
    assert reshoot._directed("BASE", None) == "BASE"
    assert reshoot._directed("BASE", (BRIEF, DIRECTION)) == direct.directed_prompt("BASE", BRIEF, DIRECTION)


def test_combined_score():
    assert direct.combined_score(0.9, None, 0.4) == 0.9
    assert direct.combined_score(1.0, 0.5, 0.4) == pytest.approx(0.8)


# --------------------------------------------------------------------------- agents (fake Gemini, real cache)


class FakeModels:
    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply, self.calls = reply, []

    def generate_content(self, model: str, contents: list[Any], config: Any) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        usage = SimpleNamespace(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=99)
        return SimpleNamespace(text=json.dumps(self.reply), usage_metadata=usage)


@pytest.fixture
def fake_gemini(monkeypatch, tmp_path):
    def install(reply: dict[str, Any]) -> FakeModels:
        models = FakeModels(reply)
        monkeypatch.setattr(direct, "get_client", lambda: SimpleNamespace(models=models))
        return models

    monkeypatch.setattr(cache, "_cache_dir", lambda: tmp_path / "cache")
    return install


def test_analyze_scene_uploads_once_and_caches(scene, fake_gemini, monkeypatch, tmp_path):
    from godshot import ingest

    models = fake_gemini(BRIEF.model_dump())
    uploads = []
    monkeypatch.setattr(ingest, "upload", lambda clip: uploads.append(clip) or "files/fake")
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"video")
    b1 = direct.analyze_scene(clip, scene)
    b2 = direct.analyze_scene(clip, scene)
    assert b1 == b2 == BRIEF
    assert len(models.calls) == 1 and len(uploads) == 1  # second run: cache hit, no upload, no call
    level = models.calls[0]["config"].thinking_config.thinking_level
    assert getattr(level, "value", level) == "HIGH"
    prompt = models.calls[0]["contents"][1]
    assert "must_preserve" in prompt and scene.actors[0].visual_description in prompt


def test_direct_sends_brief_and_diff(scene, fake_gemini):
    models = fake_gemini(DIRECTION.model_dump())
    d = direct.direct(BRIEF, scene, _stretched(scene))
    assert d == DIRECTION
    sent = models.calls[0]["contents"][0]
    assert "SCENE BRIEF" in sent and BRIEF.logline in sent
    assert "SCENE DIFF" in sent and '"speed_ratio"' in sent


def test_prepare_writes_artifacts(scene, monkeypatch, tmp_path):
    monkeypatch.setattr(direct, "analyze_scene", lambda clip, so: BRIEF)
    monkeypatch.setattr(direct, "direct", lambda b, so, se, duration_s=None: DIRECTION)
    brief, dirn = direct.prepare(Path("c.mp4"), scene, _stretched(scene), tmp_path)
    assert (brief, dirn) == (BRIEF, DIRECTION)
    assert {p.name for p in (tmp_path / "direct").iterdir()} == {"brief.json", "diff.json", "direction.json"}


# --------------------------------------------------------------------------- preservation (fake Decisions)


@needs_ffmpeg
def test_preservation_scores_and_prefers_raw(monkeypatch, tmp_path):
    from godshot import verify

    labelled = tmp_path / "take_1.mp4"
    raw = reshoot.work_dir_for(labelled) / "raw.mp4"
    raw.parent.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:d=2:r=24",
                    "-pix_fmt", "yuv420p", str(raw)], check=True)
    labelled.write_bytes(b"not a video")
    p = {"Is a woman wearing a red coat?": 0.9, "Is there a window with daylight?": 0.2}
    seen = []

    def fake_ask(q, images, context):
        seen.append(images)
        return CheckResult(p_yes=p[q.instructions], confidence=0.9)

    monkeypatch.setattr(verify, "ask", fake_ask)
    r = direct.preservation(labelled, BRIEF, work_dir=tmp_path / "pres")
    assert r["score"] == pytest.approx(0.55) and r["kept"] == 1 and r["n"] == 2
    assert all(len(imgs) == 3 for imgs in seen)
    assert json.loads((tmp_path / "pres" / "preservation.json").read_text())["kept"] == 1


# --------------------------------------------------------------------------- S12 hook: ranking


def test_reshoot_best_direct_ranks_on_combined_score(scene, monkeypatch, tmp_path):
    conf = {0: 0.95, 1: 0.85}  # take 1 follows the edit best ...
    pres = {0: 0.20, 1: 0.90}  # ... but take 2 keeps the scene: combined 0.65 vs 0.87
    directions = []

    def fake_reshoot(clip, so, se, out, take=0, direction=None, **kw):
        directions.append(direction)
        out = Path(out)
        reshoot.work_dir_for(out).mkdir(parents=True, exist_ok=True)
        (reshoot.work_dir_for(out) / "raw.mp4").write_bytes(b"raw")
        out.write_bytes(f"take{take}".encode())
        return out

    def take_of(path) -> int:
        return int(Path(path).parent.name.split("_")[1]) - 1

    monkeypatch.setattr(reshoot, "reshoot", fake_reshoot)
    monkeypatch.setattr(reshoot, "conformance", lambda raw, s, work_dir=None: {
        "score": conf[take_of(raw)], "hits": 1, "usable": 1, "checks": []})
    monkeypatch.setattr(direct, "prepare", lambda *a, **k: (BRIEF, DIRECTION))
    monkeypatch.setattr(direct, "preservation", lambda raw, b, work_dir=None: {
        "score": pres[take_of(raw)], "kept": 1, "n": 2, "items": []})
    r = reshoot.reshoot_best(Path("c.mp4"), scene, scene, tmp_path, n=2, direct=True)
    assert r["best_take"] == 2 and r["combined"] == pytest.approx(0.87)
    assert all(d == (BRIEF, DIRECTION) for d in directions)
    assert r["directed"] is True and json.loads((tmp_path / "reshoot_best.json").read_text())["kept"] == "1/2"


def test_reshoot_best_default_unchanged(scene, monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(direct, "prepare", lambda *a, **k: called.append("prepare"))
    monkeypatch.setattr(direct, "analyze_scene", lambda *a, **k: called.append("analyze"))

    def fake_reshoot(clip, so, se, out, take=0, direction=None, **kw):
        assert direction is None
        out = Path(out)
        reshoot.work_dir_for(out).mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return out

    monkeypatch.setattr(reshoot, "reshoot", fake_reshoot)
    monkeypatch.setattr(reshoot, "conformance", lambda raw, s, work_dir=None: {
        "score": 0.5, "hits": 1, "usable": 2, "checks": []})
    r = reshoot.reshoot_best(Path("c.mp4"), scene, scene, tmp_path, n=1)
    assert called == [] and "preservation" not in r
