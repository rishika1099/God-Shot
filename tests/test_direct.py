"""S14 tests: diff, interpreter lock/release, prompt budget, scoring, critic loop, S12 hook. Offline."""

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
WOMAN = "the woman in the red coat"

BRIEF = direct.SceneBrief(
    logline="A woman in a red coat crosses the room to the window.",
    intent="Her slow walk shows hesitation before she looks outside.",
    beats=[direct.Beat(t0=0, t1=6, who=WOMAN, action="walks to the window", detail="eyes down")],
    performances=[direct.Performance(who=WOMAN, gait="slow walk", pace="0.6 m/s", posture="hunched",
                                     emotion="hesitant", wardrobe="long red wool coat, black boots")],
    environment="cold blue daylight from the window, dust in the air",
    physics=["the coat swings with each step"],
    must_preserve=[
        direct.PreserveItem(element="the red coat", why="identity", check="Is a woman wearing a red coat?"),
        direct.PreserveItem(element="the window", why="goal", check="Is there a window with daylight?"),
        direct.PreserveItem(element="the slow walk", why="mood", check="Is the woman walking slowly?"),
    ],
)
DIRECTION = direct.Direction(
    change_summary="Her path is twice as long in the same time.",
    adaptations=[direct.Adaptation(who=WOMAN, change="path 2× longer", reasoning="1.2 m/s is brisk",
                                   behavior="brisk but heavy steps")],
    preserved=["the red coat: unchanged"],
    performance_prompt="The woman in the red coat walks briskly but heavily to the window, eyes down.",
)
SPEC = direct.ChangeSpec(
    summary="She runs to the window at night.",
    changes=[direct.Change(kind="blocking", what="longer path", source="map"),
             direct.Change(kind="environment", what="night", source="request")],
    released=[direct.Release(index=2, reason="the user asked her to run")],
    success_checks=[direct.SuccessCheck(element="night", check="Is it night outside the window?")],
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
    d = direct.scene_diff(scene, _stretched(scene))
    person = d["people"][0]
    assert person["status"] == "changed" and person["who"] == scene.actors[0].visual_description
    assert person["speed_ratio"] == pytest.approx(2.0, abs=0.15)
    assert d["objects"][0]["status"] == "moved" and d["objects"][0]["moved_m"] == pytest.approx(1.0)
    assert d["camera"]["changed"] is False
    text = json.dumps(d)
    assert all(f'"{e.id}"' not in text for e in [*scene.actors, *scene.objects])


def test_scene_diff_added_removed_and_camera(scene):
    edited = scene.model_copy(deep=True)
    gone, gone_obj = edited.actors.pop(0), edited.objects.pop(0)
    for k in edited.camera.track:
        k.yaw_deg = -k.yaw_deg  # pan the other way
    d = direct.scene_diff(scene, edited)
    assert {"who": gone.visual_description, "status": "removed"} in d["people"]
    assert {"what": gone_obj.visual_description, "status": "removed"} in d["objects"]
    assert d["camera"]["changed"] is True
    assert direct.scene_diff(edited, scene)["people"][0]["status"] == "added"


def test_unchanged_scene_diff(scene):
    d = direct.scene_diff(scene, scene.model_copy(deep=True))
    assert all(p["status"] == "unchanged" for p in d["people"]) and d["objects"] == []


def test_map_summary_has_no_ids(scene):
    text = direct._map_summary(scene)
    assert all(f'"{e.id}"' not in text for e in [*scene.actors, *scene.objects])


# --------------------------------------------------------------------------- interpreter: lock / release


def test_normalize_spec_every_item_locked_or_released():
    raw = SPEC.model_copy(update={"locked": [0, 2, 7], "released": [
        direct.Release(index=2, reason="run"), direct.Release(index=9, reason="out of range")]})
    s = direct.normalize_spec(raw, 3)
    assert s.locked == [0, 1] and [r.index for r in s.released] == [2]  # released wins; 1 defaults to locked
    assert [p.element for p in direct.locked_items(BRIEF, s)] == ["the red coat", "the window"]
    assert len(direct.locked_items(BRIEF, None)) == 3


# --------------------------------------------------------------------------- prompt composition


def test_compose_keeps_geometry_first_and_verbatim():
    base = "Re-render this previs.\n00:00–00:04: the camera dollies in about 2 m."
    p = direct.compose(base, (BRIEF, DIRECTION))
    assert p.startswith(base) and direct.CONFLICT_RULE in p and DIRECTION.performance_prompt in p
    assert "MUST KEEP: the red coat; the window; the slow walk." in p
    assert "long red wool coat" in p and "cold blue daylight" in p
    assert direct.directed_prompt(base, BRIEF, DIRECTION) == p
    assert reshoot._directed(base, None) == base and reshoot._directed(base, (BRIEF, DIRECTION)) == p


def test_compose_respects_spec_and_corrections():
    d = DIRECTION.model_copy(update={"scene_changes_prompt": "It is night; moonlight through the window."})
    pack = direct.Pack(BRIEF, d, spec=direct.normalize_spec(SPEC, 3), corrections=["Land before walking."])
    p = direct.compose("BASE", pack)
    assert "MUST KEEP: the red coat; the window." in p  # released "slow walk" is not kept
    assert "REQUESTED CHANGES: It is night" in p and "CORRECTIONS FROM THE PREVIOUS TAKE: Land before" in p
    assert "LIGHT AND ATMOSPHERE" not in p  # environment was requested to change


def test_compose_drops_low_priority_sections_first(monkeypatch):
    base = "B" * 300
    full = direct.compose(base, (BRIEF, DIRECTION))
    monkeypatch.setattr(direct, "MAX_PROMPT_CHARS", len(full) - 1)
    p = direct.compose(base, (BRIEF, DIRECTION))
    assert p.startswith(base) and "PERFORMANCE" in p and "SCENE:" not in p  # SCENE has the lowest priority
    monkeypatch.setattr(direct, "MAX_PROMPT_CHARS", 10)
    assert direct.compose(base, (BRIEF, DIRECTION)) == base  # the base is never cut


def test_combined_score():
    assert direct.combined_score(0.9, None, 0.4) == 0.9
    assert direct.combined_score(1.0, 0.5, 0.4) == pytest.approx(0.8)
    assert direct.combined_score(1.0, 0.5, 0.4, fulfil=0.9) == pytest.approx(0.88)


def test_score_summary():
    conf = {"score": 0.8, "checks": [{"template": "PAN", "usable": True, "hit": False},
                                     {"template": "SIDE", "usable": True, "hit": True},
                                     {"template": "PAN", "usable": False, "hit": False}]}  # fmt: skip
    pres = {"items": [{"element": "coat", "kept": False}, {"element": "window", "kept": True}]}
    s = direct.score_summary(conf, pres, None)
    assert s["map_misses_by_template"] == {"PAN": 1} and s["locked_elements_lost"] == ["coat"]
    assert s["requested_changes_missing"] == []


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
    assert direct.analyze_scene(clip, scene) == direct.analyze_scene(clip, scene) == BRIEF
    assert len(models.calls) == 1 and len(uploads) == 1  # second run: cache hit, no upload, no call
    level = models.calls[0]["config"].thinking_config.thinking_level
    assert getattr(level, "value", level) == "HIGH"


def test_interpret_uses_cheap_model_and_normalizes(scene, fake_gemini):
    from godshot.config import load_config

    models = fake_gemini(SPEC.model_dump())
    diff = direct.scene_diff(scene, _stretched(scene))
    s = direct.interpret(BRIEF, diff, request="make it night, she runs")
    assert models.calls[0]["model"] == load_config().MODEL_CHEAP
    assert s.locked == [0, 1] and s.released[0].index == 2
    sent = models.calls[0]["contents"][0]
    assert "make it night, she runs" in sent and "[2] the slow walk" in sent


def test_direct_sends_brief_diff_and_spec(scene, fake_gemini):
    models = fake_gemini(DIRECTION.model_dump())
    spec = direct.normalize_spec(SPEC, 3)
    assert direct.direct(BRIEF, scene, _stretched(scene), spec=spec, request="night") == DIRECTION
    sent = models.calls[0]["contents"][0]
    assert BRIEF.logline in sent and '"speed_ratio"' in sent and "CHANGE SPEC" in sent
    assert "RELEASED" in sent and "the slow walk: the user asked her to run" in sent


def test_prepare_writes_artifacts(scene, monkeypatch, tmp_path):
    monkeypatch.setattr(direct, "analyze_scene", lambda clip, so: BRIEF)
    monkeypatch.setattr(direct, "interpret", lambda b, diff, request="": SPEC)
    monkeypatch.setattr(direct, "direct", lambda b, so, se, dur=None, spec=None, request="": DIRECTION)
    pack = direct.prepare(Path("c.mp4"), scene, _stretched(scene), tmp_path, request="night")
    assert (pack.brief, pack.direction, pack.spec, pack.request) == (BRIEF, DIRECTION, SPEC, "night")
    names = {p.name for p in (tmp_path / "direct").iterdir()}
    assert names == {"brief.json", "diff.json", "change_spec.json", "direction.json"}


def test_critique_watches_the_unlabelled_take(fake_gemini, monkeypatch, tmp_path):
    from godshot import ingest

    crit = direct.Critique(verdict="revise", issues=[direct.Issue(kind="action", at="00:00–00:01",
                           problem="no landing", fix="land first")], corrections="Land first.")  # fmt: skip
    models = fake_gemini(crit.model_dump())
    uploaded = []
    monkeypatch.setattr(ingest, "upload", lambda v: uploaded.append(Path(v)) or "files/take")
    take = tmp_path / "take_1.mp4"
    raw = reshoot.work_dir_for(take) / "raw.mp4"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"raw video")
    take.write_bytes(b"labelled")
    pack = direct.Pack(BRIEF, DIRECTION, spec=direct.normalize_spec(SPEC, 3), request="night")
    assert direct.critique(take, pack, {"map_conformance": 0.9}) == crit
    assert uploaded == [raw]
    sent = models.calls[0]["contents"][1]
    assert "LOCKED ELEMENTS" in sent and "- the slow walk" not in sent and '"map_conformance": 0.9' in sent


# --------------------------------------------------------------------------- scoring (fake Decisions)


@pytest.fixture
def take_video(tmp_path) -> Path:
    labelled = tmp_path / "take_1.mp4"
    raw = reshoot.work_dir_for(labelled) / "raw.mp4"
    raw.parent.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:d=2:r=24",
                    "-pix_fmt", "yuv420p", str(raw)], check=True)
    labelled.write_bytes(b"not a video")
    return labelled


@needs_ffmpeg
def test_preservation_scores_locked_items_only(take_video, monkeypatch, tmp_path):
    from godshot import verify

    p = {"Is a woman wearing a red coat?": 0.9, "Is there a window with daylight?": 0.2}
    seen: list[str] = []

    def fake_ask(q, images, context):
        assert len(images) == 3
        seen.append(q.instructions)
        return CheckResult(p_yes=p[q.instructions], confidence=0.9)

    monkeypatch.setattr(verify, "ask", fake_ask)
    spec = direct.normalize_spec(SPEC, 3)
    r = direct.preservation(take_video, BRIEF, work_dir=tmp_path / "pres", spec=spec)
    assert r["score"] == pytest.approx(0.55) and (r["kept"], r["n"]) == (1, 2)
    assert "Is the woman walking slowly?" not in seen  # released → not scored
    assert r["source"].endswith("raw.mp4")


@needs_ffmpeg
def test_fulfilment(take_video, monkeypatch, tmp_path):
    from godshot import verify

    monkeypatch.setattr(verify, "ask", lambda q, images, context: CheckResult(p_yes=0.8, confidence=0.8))
    r = direct.fulfilment(take_video, SPEC, work_dir=tmp_path / "ful")
    assert r["score"] == pytest.approx(0.8) and r["kept"] == 1
    assert direct.fulfilment(take_video, SPEC.model_copy(update={"success_checks": []})) is None


# --------------------------------------------------------------------------- S12 hook: ranking, critic, retry


def _take_of(path) -> int:
    return int(Path(path).parent.name.split("_")[1]) - 1


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Fake Omni/conformance/scores; returns a dict to configure scores and inspect calls."""
    st: dict[str, Any] = {"conf": {}, "pres": {}, "packs": {}, "fail_once": set()}

    def fake_reshoot(clip, so, se, out, take=0, direction=None, **kw):
        if take in st["fail_once"]:
            st["fail_once"].discard(take)
            raise reshoot.OmniError("INVALID_ARGUMENT: There was a problem processing your request.")
        st["packs"][take] = direction
        out = Path(out)
        reshoot.work_dir_for(out).mkdir(parents=True, exist_ok=True)
        (reshoot.work_dir_for(out) / "raw.mp4").write_bytes(b"raw")
        out.write_bytes(f"take{take}".encode())
        return out

    monkeypatch.setattr(reshoot, "reshoot", fake_reshoot)
    monkeypatch.setattr(reshoot, "conformance", lambda raw, s, work_dir=None: {
        "score": st["conf"].get(_take_of(raw), 0.9), "hits": 1, "usable": 1, "checks": []})
    monkeypatch.setattr(direct, "prepare", lambda *a, **k: direct.Pack(
        BRIEF, DIRECTION, spec=direct.normalize_spec(SPEC, 3), request=k.get("request", "")))
    monkeypatch.setattr(direct, "preservation", lambda raw, b, work_dir=None, spec=None: {
        "score": st["pres"].get(_take_of(raw), 0.5), "kept": 1, "n": 2, "items": []})
    monkeypatch.setattr(direct, "fulfilment", lambda raw, spec, work_dir=None: {
        "score": 0.5, "kept": 1, "n": 1, "items": []})
    return st


def test_reshoot_best_direct_ranks_on_combined_score(scene, fake_pipeline, tmp_path):
    fake_pipeline["conf"] = {0: 0.95, 1: 0.85}  # take 1 follows the map best ...
    fake_pipeline["pres"] = {0: 0.20, 1: 0.90}  # ... but take 2 keeps the scene
    r = reshoot.reshoot_best(Path("c.mp4"), scene, scene, tmp_path, n=2, direct=True)
    # take 1: 0.6·0.95 + 0.4·mean(0.2, 0.5) = 0.71; take 2: 0.6·0.85 + 0.4·mean(0.9, 0.5) = 0.79
    assert r["best_take"] == 2 and r["combined"] == pytest.approx(0.79)
    assert all(isinstance(p, direct.Pack) for p in fake_pipeline["packs"].values())
    assert json.loads((tmp_path / "reshoot_best.json").read_text())["kept"] == "1/2"


def test_request_implies_direct(scene, fake_pipeline, tmp_path):
    r = reshoot.reshoot_best(Path("c.mp4"), scene, scene, tmp_path, n=1, request="make it night")
    assert r["directed"] is True and r["request"] == "make it night" and r["fulfilment"] == 0.5
    assert fake_pipeline["packs"][0].request == "make it night"


def test_critic_loop_adds_corrected_take(scene, fake_pipeline, monkeypatch, tmp_path):
    fake_pipeline["pres"] = {0: 0.3, 1: 0.95}
    verdicts = iter([
        direct.Critique(verdict="revise", issues=[direct.Issue(kind="action", at="00:00–00:01",
                        problem="no landing", fix="land")], corrections="Land at the rope first."),
        direct.Critique(verdict="accept"),
    ])  # fmt: skip
    seen_packs = []
    def fake_critique(video, pack, scores):
        seen_packs.append(pack)
        return next(verdicts)

    monkeypatch.setattr(direct, "critique", fake_critique)
    r = reshoot.reshoot_best(Path("c.mp4"), scene, scene, tmp_path, n=1, direct=True, critic_rounds=3)
    assert [c["verdict"] for c in r["critiques"]] == ["revise", "accept"]  # stops at accept
    assert fake_pipeline["packs"][1].corrections == ["Land at the rope first."]  # take 2 got the fix
    assert r["best_take"] == 2 and len(r["takes"]) == 2
    assert seen_packs[1].corrections == ["Land at the rope first."]
    assert json.loads((tmp_path / "direct" / "critique_1.json").read_text())["verdict"] == "revise"


def test_failed_omni_take_is_retried_once(scene, fake_pipeline, tmp_path):
    fake_pipeline["fail_once"] = {0}
    r = reshoot.reshoot_best(Path("c.mp4"), scene, scene, tmp_path, n=1, direct=True)
    assert r["best_take"] == 1 and 0 in fake_pipeline["packs"]


def test_reshoot_best_default_unchanged(scene, monkeypatch, tmp_path):
    called = []
    for name in ("prepare", "analyze_scene", "preservation", "critique"):
        monkeypatch.setattr(direct, name, lambda *a, _n=name, **k: called.append(_n))

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
