"""S12: blockout guide video, Omni request shape, conformance scoring (no network)."""

from __future__ import annotations

import base64
import json
import math
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from godshot import reshoot
from godshot.config import load_config
from godshot.schema import CheckResult, SceneV1, mmss, qkey

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
W, H = 320, 180


def _scene(yaw_end: float = 0.0, box_at: tuple[float, float] = (-2.0, 6.0), duration: float = 2.0) -> SceneV1:
    keys = [
        {"t": t, "mmss": mmss(t), "x": 0.0, "y": 0.0, "yaw_deg": yaw_end * t / duration}
        for t in (0.0, 1.0, 2.0)
    ]
    return SceneV1.model_validate(
        {
            "clip": {"id": "t", "duration_s": duration, "fps": 24, "width": W, "height": H},
            "room": {"outline": [[-8, -2], [8, -2], [8, 12], [-8, 12]]},
            "objects": [
                {"id": "box", "type": "cabinet", "visual_description": "the bright green box",
                 "center": list(box_at), "size": [0.6, 0.6], "color": "#00ff00"}
            ],
            "camera": {"hfov_deg": 60, "track": keys},
        }
    )  # fmt: skip


def _green_centroid_x(img) -> float:
    px = img.load()
    xs = [x for x in range(img.width) for y in range(img.height) if px[x, y][1] > 180 and px[x, y][0] < 120]
    assert xs, "box not visible in blockout frame"
    return sum(xs) / len(xs)


def test_blockout_projection_matches_pinhole_geometry() -> None:
    scene = _scene()
    img = reshoot._blockout_frame(scene, 0.0, W, H)
    bearing = math.degrees(math.atan2(2.0, 6.0))  # box at (-2, 6): to the camera's LEFT
    f = (W / 2) / math.tan(math.radians(30))
    expected_u = W / 2 - f * math.tan(math.radians(bearing))
    assert _green_centroid_x(img) == pytest.approx(expected_u, abs=4)  # within a few pixels


def test_blockout_follows_the_camera_track() -> None:
    scene = _scene(yaw_end=20.0)  # camera pans LEFT 20° over 2 s → the box slides RIGHT on screen
    u0 = _green_centroid_x(reshoot._blockout_frame(scene, 0.0, W, H))
    u1 = _green_centroid_x(reshoot._blockout_frame(scene, 2.0, W, H))
    f = (W / 2) / math.tan(math.radians(30))
    b0 = math.degrees(math.atan2(2.0, 6.0))
    expected_u1 = W / 2 - f * math.tan(math.radians(b0 - 20.0))
    assert u1 > u0
    assert u1 == pytest.approx(expected_u1, abs=4)


def test_blockout_hides_invisible_actor() -> None:
    scene = _scene()
    data = scene.model_dump(by_alias=True)
    data["actors"] = [
        {"id": "a1", "visual_description": "the man", "color": "#ff0000",
         "track": [{"t": 0, "x": 0, "y": 4, "visible": False}, {"t": 2, "x": 0, "y": 4, "visible": False}]}
    ]  # fmt: skip
    img = reshoot._blockout_frame(SceneV1.model_validate(data), 1.0, W, H)
    px = img.load()
    assert not any(px[x, y][0] > 200 and px[x, y][1] < 80 for x in range(W) for y in range(H))


@needs_ffmpeg
def test_render_blockout_writes_video(tmp_path: Path) -> None:
    out = reshoot.render_blockout(_scene(yaw_end=20), tmp_path / "b.mp4", fps=12, size=(W, H))
    from godshot import ingest

    c = ingest.probe(out)
    assert (c.width, c.height) == (W, H) and c.duration_s == pytest.approx(2.0, abs=0.1)
    with pytest.raises(ValueError):
        reshoot.render_blockout(_scene(), tmp_path / "bad.mp4", t0=1.0, t1=1.0)


# --------------------------------------------------------------------------- Omni request shape


class FakeInteractions:
    def __init__(self) -> None:
        self.kwargs: dict = {}
        self.video = b"\x00\x00\x00 ftypmp42-fake-video"

    def _done(self) -> SimpleNamespace:
        block = SimpleNamespace(type="video", data=base64.b64encode(self.video).decode(), uri=None)
        return SimpleNamespace(
            id="i1",
            status="completed",
            errors=None,
            steps=[SimpleNamespace(type="model_output", error=None, content=[block])],
        )

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(id="i1", status="in_progress", errors=None, steps=[])

    def get(self, *, id: str):
        return self._done()


@pytest.fixture
def fake_omni(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeInteractions:
    monkeypatch.setenv("GODSHOT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GODSHOT_OFFLINE", raising=False)
    load_config.cache_clear()
    fake = FakeInteractions()
    monkeypatch.setattr(reshoot, "get_client", lambda: SimpleNamespace(interactions=fake))
    yield fake
    load_config.cache_clear()


def test_generate_video_image_mode(fake_omni: FakeInteractions, tmp_path: Path) -> None:
    out = reshoot.generate_video([b"jpeg"], "arc right", tmp_path / "o.mp4", poll_s=0)
    assert out.read_bytes() == fake_omni.video
    kw = fake_omni.kwargs
    assert kw["background"] is True and "generation_config" not in kw
    assert kw["response_format"] == {
        "type": "video", "delivery": "inline", "duration": "5s", "resolution": "360p", "aspect_ratio": "16:9"
    }  # fmt: skip
    assert [b["type"] for b in kw["input"]] == ["image", "text"]


def test_generate_video_edit_mode_omits_duration_and_aspect(
    fake_omni: FakeInteractions, tmp_path: Path
) -> None:
    reshoot.generate_video([b"jpeg"], "re-render", tmp_path / "o.mp4", guide=b"mp4", task="edit", poll_s=0)
    kw = fake_omni.kwargs
    assert kw["generation_config"] == {"video_config": {"task": "edit"}}
    assert kw["response_format"] == {"type": "video", "delivery": "inline", "resolution": "360p"}
    assert [b["type"] for b in kw["input"]] == ["video", "image", "text"]  # guide video first


def test_generate_video_is_cached(fake_omni: FakeInteractions, tmp_path: Path, monkeypatch) -> None:
    reshoot.generate_video([b"jpeg"], "p", tmp_path / "a.mp4", poll_s=0)
    monkeypatch.setattr(reshoot, "_omni_call", lambda *a, **k: pytest.fail("cache miss"))
    assert (
        reshoot.generate_video([b"jpeg"], "p", tmp_path / "b.mp4", poll_s=0).read_bytes() == fake_omni.video
    )


def test_omni_failure_surfaces_error_codes(fake_omni: FakeInteractions, tmp_path: Path, monkeypatch) -> None:
    err = SimpleNamespace(code="INVALID_ARGUMENT", message="There was a problem processing your request.")
    monkeypatch.setattr(
        fake_omni, "get", lambda *, id: SimpleNamespace(id=id, status="failed", errors=[err], steps=[])
    )
    with pytest.raises(reshoot.OmniError, match="INVALID_ARGUMENT"):
        reshoot.generate_video([b"jpeg"], "x", tmp_path / "o.mp4", poll_s=0)


# --------------------------------------------------------------------------- conformance scoring


@needs_ffmpeg
def test_conformance_scores_predict_vs_observed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from godshot import checks as checks_mod
    from godshot import verify as verify_mod

    scene = SceneV1.load(Path(__file__).resolve().parent.parent / "mocks" / "scene_mock.json")
    clip = reshoot.render_blockout(scene, tmp_path / "gen.mp4", fps=8, size=(W, H))
    flip = {"n": 0}

    def fake_verify(bank, frames_dir):
        answers = {}
        for c in bank:
            assert all((Path(frames_dir) / Path(p).name).exists() for p in c.input_ref.paths)
            pred = checks_mod.predict(scene, c)
            if c.question.type == "noul":
                yes = bool(pred) if flip["n"] else not bool(pred)
                flip["n"] += 1
                answers[qkey(c)] = CheckResult(p_yes=0.95 if yes else 0.05, confidence=0.95)
            else:
                answers[qkey(c)] = CheckResult(choice_probs={pred or "other": 0.9}, confidence=0.9)
        return answers

    monkeypatch.setattr(verify_mod, "verify", fake_verify)
    r = reshoot.conformance(clip, scene, work_dir=tmp_path / "conf")
    assert r["n_checks"] >= 20 and r["usable"] == r["n_checks"]
    assert r["hits"] == r["usable"] - 1  # exactly one noul answer was flipped
    assert r["score"] == pytest.approx(r["hits"] / r["usable"], abs=1e-3)
    assert (tmp_path / "conf" / "conformance.json").exists()


# --------------------------------------------------------------------------- shot description


MOCK = Path(__file__).resolve().parent.parent / "mocks" / "scene_mock.json"


def _arc_scene() -> SceneV1:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "blockout_spike", Path(__file__).resolve().parent.parent / "scripts" / "blockout_spike.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.arc_scene()


def test_describe_shot_mock_is_exact_and_deterministic() -> None:
    scene = SceneV1.load(MOCK)
    text = reshoot.describe_shot(scene)
    assert text == reshoot.describe_shot(scene)  # same map → same prompt
    assert "00:00–00:04: the camera moves forward about 2 m" in text
    assert "00:04–00:08: the camera pans left about 40°" in text
    assert "the green wooden door comes into view from the left edge" in text
    assert "At the end: the green wooden door in the centre" in text
    assert "the man in the red hoodie walks from left to right" in text
    assert "no cuts" in text and "_1" not in text and "a1" not in text  # never leak ids


def test_describe_shot_arc_names_the_subject() -> None:
    text = reshoot.describe_shot(_arc_scene())
    assert "orbits about 45° to the right around the grey-haired man in the plaid shirt" in text
    assert "the dark wooden cabinet leaves the frame on the right" in text


def test_describe_shot_respects_duration_cap() -> None:
    text = reshoot.describe_shot(SceneV1.load(MOCK), duration_s=4.0)
    assert text.startswith("A 4-second shot") and "pans left" not in text


def test_color_names_and_palette_dedupe() -> None:
    assert reshoot.color_name("#e74c3c") == "red"
    assert reshoot.color_name("#2e7d32") == "green"
    assert reshoot.color_name("#c9a227") == "gold"
    scene = SceneV1.load(MOCK)
    data = scene.model_dump(by_alias=True)
    data["objects"][0]["color"] = "#e74c3c"  # door now red, same as the actor
    pal = reshoot.blockout_palette(SceneV1.model_validate(data))
    names = [reshoot.color_name(c) for c in pal.values()]
    assert len(names) == len(set(names))  # every entity gets a distinct colour name


def test_edit_prompt_has_a_legend_for_every_entity() -> None:
    scene = _arc_scene()
    text = reshoot.edit_prompt(scene, reshoot.blockout_palette(scene))
    assert "the red figure is the grey-haired man in the plaid shirt" in text
    assert "the gold box is the large golden machine" in text
    assert "the brown box is the dark wooden cabinet" in text
    assert "EXACTLY as in the input video" in text


# --------------------------------------------------------------------------- refs, label, reshoot


@pytest.fixture(scope="module")
def orig_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("orig")
    return reshoot.render_blockout(SceneV1.load(MOCK), d / "mock_001.mp4", fps=8, size=(W, H))


@needs_ffmpeg
def test_reference_images(orig_clip: Path, tmp_path: Path) -> None:
    refs = reshoot.reference_images(orig_clip, SceneV1.load(MOCK), tmp_path)
    assert 3 <= len(refs) <= reshoot.MAX_REFS
    assert all(r.exists() and r.stat().st_size > 0 for r in refs)
    assert any(r.name.startswith("crop_a1") for r in refs)  # the actor gets a crop


@needs_ffmpeg
def test_burn_label_marks_the_video(orig_clip: Path, tmp_path: Path) -> None:
    from PIL import ImageChops

    from godshot import ingest

    out = reshoot.burn_label(orig_clip, tmp_path / "labelled.mp4")
    a = Image.open(ingest.frame_at(orig_clip, 1.0, tmp_path / "a")).convert("RGB")
    b = Image.open(ingest.frame_at(out, 1.0, tmp_path / "b")).convert("RGB")
    box = (0, 0, W // 3, H // 6)
    assert ImageChops.difference(a.crop(box), b.crop(box)).getbbox() is not None  # badge drawn top-left
    assert not list(tmp_path.glob("*.label.png"))


@needs_ffmpeg
def test_reshoot_blockout_mode_end_to_end(
    orig_clip: Path, fake_omni: FakeInteractions, tmp_path: Path
) -> None:
    fake_omni.video = orig_clip.read_bytes()  # a real mp4 so labelling works
    scene = SceneV1.load(MOCK)
    out = reshoot.reshoot(orig_clip, scene, scene, tmp_path / "r" / "reshoot.mp4", duration="4s")
    assert out.exists()
    kw = fake_omni.kwargs
    assert kw["generation_config"] == {"video_config": {"task": "edit"}}
    assert kw["input"][0]["type"] == "video" and kw["input"][-1]["type"] == "text"
    work = reshoot.work_dir_for(out)
    meta = json.loads((work / "reshoot.json").read_text())
    assert meta["mode"] == "blockout" and meta["label"] == reshoot.AI_LABEL and meta["duration_s"] == 4.0
    from godshot import ingest

    assert ingest.probe(work / "blockout.mp4").duration_s == pytest.approx(4.0, abs=0.15)  # guide cut to 4 s


@needs_ffmpeg
def test_reshoot_text_mode(orig_clip: Path, fake_omni: FakeInteractions, tmp_path: Path) -> None:
    fake_omni.video = orig_clip.read_bytes()
    scene = SceneV1.load(MOCK)
    reshoot.reshoot(orig_clip, scene, scene, tmp_path / "t.mp4", mode="text", duration="8s")
    kw = fake_omni.kwargs
    assert "generation_config" not in kw and kw["response_format"]["duration"] == "8s"
    assert "video" not in [b["type"] for b in kw["input"]]
    assert "00:04–00:08: the camera pans left about 40°" in kw["input"][-1]["text"]
    with pytest.raises(ValueError):
        reshoot.reshoot(orig_clip, scene, scene, tmp_path / "x.mp4", mode="bogus")


def test_reshoot_best_keeps_the_highest_conformance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scores = {0: 0.70, 1: 0.95, 2: 0.80}

    def fake_reshoot(clip, so, se, out, take=0, **kw):
        out = Path(out)
        reshoot.work_dir_for(out).mkdir(parents=True, exist_ok=True)
        (reshoot.work_dir_for(out) / "raw.mp4").write_bytes(b"raw")
        out.write_bytes(f"take{take}".encode())
        return out

    def fake_conf(raw, scene, work_dir=None):
        take = int(Path(raw).parent.name.split("_")[1]) - 1
        return {
            "score": scores[take],
            "hits": int(scores[take] * 20),
            "usable": 20,
            "checks": [{"take": take}],
        }

    monkeypatch.setattr(reshoot, "reshoot", fake_reshoot)
    monkeypatch.setattr(reshoot, "conformance", fake_conf)
    scene = SceneV1.load(MOCK)
    r = reshoot.reshoot_best(Path("clip.mp4"), scene, scene, tmp_path, n=3)
    assert r["best_take"] == 2 and r["score"] == 0.95
    assert (tmp_path / "reshoot_best.mp4").read_bytes() == b"take1"
    assert json.loads((tmp_path / "reshoot_best.json").read_text())["best_take"] == 2


@needs_ffmpeg
def test_conformance_prefers_unlabelled_raw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from godshot import verify as verify_mod

    scene = SceneV1.load(MOCK)
    labelled = tmp_path / "reshoot.mp4"
    raw = reshoot.work_dir_for(labelled) / "raw.mp4"
    raw.parent.mkdir(parents=True)
    reshoot.render_blockout(scene, raw, fps=8, size=(W, H))
    labelled.write_bytes(b"not a video: must not be read")
    monkeypatch.setattr(verify_mod, "verify", lambda bank, frames_dir: {})
    r = reshoot.conformance(labelled, scene, work_dir=tmp_path / "c")
    assert r["source"] == str(raw) and r["usable"] == 0


# --------------------------------------------------------------------------- full length + performance


BEATS = [
    {"t0": 0.0, "t1": 3.0, "who": "the man in the red hoodie", "action": "waves at someone off-screen"},
    {"t0": 3.0, "t1": 6.5, "who": "the man in the red hoodie", "action": "turns his head left and laughs"},
]


@needs_ffmpeg
def test_reshoot_keeps_the_original_length_by_default(
    orig_clip: Path, fake_omni: FakeInteractions, tmp_path: Path
) -> None:
    from godshot import ingest

    fake_omni.video = orig_clip.read_bytes()
    scene = SceneV1.load(MOCK)  # 10 s
    out = reshoot.reshoot(orig_clip, scene, scene, tmp_path / "full.mp4")
    meta = json.loads((reshoot.work_dir_for(out) / "reshoot.json").read_text())
    assert meta["source_duration_s"] == 10.0 and meta["duration_s"] == 10.0 and meta["time_scale"] == 1.0
    assert ingest.probe(reshoot.work_dir_for(out) / "blockout.mp4").duration_s == pytest.approx(
        10.0, abs=0.15
    )
    assert "A 10-second shot" in fake_omni.kwargs["input"][-1]["text"]


@needs_ffmpeg
def test_reshoot_too_long_is_time_scaled_not_cut(
    orig_clip: Path, fake_omni: FakeInteractions, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from godshot import ingest

    fake_omni.video = orig_clip.read_bytes()
    monkeypatch.setattr(reshoot, "OMNI_EDIT_MAX_S", 5.0)
    scene = SceneV1.load(MOCK)  # 10 s → must fit 5 s
    out = reshoot.reshoot(orig_clip, scene, scene, tmp_path / "fit.mp4")
    meta = json.loads((reshoot.work_dir_for(out) / "reshoot.json").read_text())
    assert meta["time_scale"] == 2.0 and meta["duration_s"] == 5.0 and meta["shot_duration_s"] == 10.0
    assert ingest.probe(reshoot.work_dir_for(out) / "blockout.mp4").duration_s == pytest.approx(5.0, abs=0.15)
    text = fake_omni.kwargs["input"][-1]["text"]
    assert (
        "A 5-second shot" in text and "00:02–00:04: the camera pans left" in text
    )  # whole shot, times halved


@needs_ffmpeg
def test_text_mode_length_is_clamped_to_omni_range(
    orig_clip: Path, fake_omni: FakeInteractions, tmp_path: Path
) -> None:
    fake_omni.video = orig_clip.read_bytes()
    scene = SceneV1.load(MOCK)
    out = reshoot.reshoot(orig_clip, scene, scene, tmp_path / "t.mp4", mode="text")
    meta = json.loads((reshoot.work_dir_for(out) / "reshoot.json").read_text())
    assert fake_omni.kwargs["response_format"]["duration"] == "8s" and meta["time_scale"] == 1.25


def test_prompt_keeps_the_performance_and_only_changes_the_camera() -> None:
    scene = SceneV1.load(MOCK)
    text = reshoot.edit_prompt(scene, reshoot.blockout_palette(scene), beats=BEATS)
    assert "they do NOT show the performance" in text
    assert "Change ONLY the camera position, angle, point of view and field of view" in text
    assert "Performance (keep EXACTLY as in the original clip; only the camera changes):" in text
    assert "00:00.00–00:03.00: the man in the red hoodie waves at someone off-screen" in text
    assert "no zoom" not in text and "stays in place" not in text


def test_fov_edit_is_described_as_a_zoom() -> None:
    scene = SceneV1.load(MOCK)
    data = scene.model_dump(by_alias=True)
    data["camera"]["hfov_deg"] = 35
    tighter = reshoot.describe_shot(SceneV1.model_validate(data), orig_hfov=60)
    assert (
        "Lens: a 35° horizontal field of view instead of the original 60° (tighter framing, like zooming in)."
        in tighter
    )
    assert "Lens:" not in reshoot.describe_shot(scene, orig_hfov=60)  # unchanged FOV → no lens line


@pytest.mark.real_beats
def test_performance_beats_parses_caches_and_clamps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from godshot import ingest

    monkeypatch.setenv("GODSHOT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GODSHOT_OFFLINE", raising=False)
    load_config.cache_clear()
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"clip bytes")
    calls = {"upload": 0, "gen": 0}
    monkeypatch.setattr(
        ingest, "upload", lambda c: calls.__setitem__("upload", calls["upload"] + 1) or "files/x"
    )
    payload = {"beats": [
        {"t0": 4.0, "t1": 30.0, "who": "the man in the red hoodie", "action": "sits down"},
        {"t0": 0.5, "t1": 2.0, "who": "the man in the red hoodie", "action": "waves"},
        {"t0": 1.0, "t1": 1.5, "who": "x", "action": "   "},
    ]}  # fmt: skip

    def generate_content(**kw):
        calls["gen"] += 1
        assert kw["config"].response_mime_type == "application/json"
        return SimpleNamespace(text=json.dumps(payload))

    monkeypatch.setattr(
        reshoot,
        "get_client",
        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
    )
    scene = SceneV1.load(MOCK)
    beats = reshoot.performance_beats(clip, scene)
    assert [b["action"] for b in beats] == ["waves", "sits down"]  # sorted, blank dropped
    assert beats[1]["t1"] == 10.0  # clamped to the clip
    assert reshoot.performance_beats(clip, scene) == beats and calls == {"upload": 1, "gen": 1}  # cached
    load_config.cache_clear()


@pytest.mark.real_beats
def test_performance_beats_failure_falls_back_to_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from godshot import ingest

    monkeypatch.setenv("GODSHOT_CACHE_DIR", str(tmp_path / "cache"))
    load_config.cache_clear()
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr(ingest, "upload", lambda c: (_ for _ in ()).throw(RuntimeError("no network")))
    assert reshoot.performance_beats(clip, SceneV1.load(MOCK)) == []
    load_config.cache_clear()
