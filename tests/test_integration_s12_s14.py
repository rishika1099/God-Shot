"""S12 × S14 integration: S12 owns camera, positions and length; with --direct the S14 director owns acting."""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from godshot import direct, reshoot
from godshot.config import load_config
from godshot.schema import SceneV1
from tests.test_direct import BRIEF, DIRECTION

MOCK = Path(__file__).resolve().parent.parent / "mocks" / "scene_mock.json"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture
def scene() -> SceneV1:
    return SceneV1.load(MOCK)


@pytest.fixture(scope="module")
def orig_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    return reshoot.render_blockout(
        SceneV1.load(MOCK), tmp_path_factory.mktemp("o") / "mock_001.mp4", fps=8, size=(320, 180)
    )


@pytest.fixture
def fake_omni(orig_clip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setenv("GODSHOT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GODSHOT_OFFLINE", raising=False)
    load_config.cache_clear()
    seen: dict = {}
    video = base64.b64encode(orig_clip.read_bytes()).decode()

    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(id="i", status="in_progress", errors=None, steps=[])

    def get(*, id):
        block = SimpleNamespace(type="video", data=video, uri=None)
        return SimpleNamespace(id=id, status="completed", errors=None,
                               steps=[SimpleNamespace(type="model_output", error=None, content=[block])])  # fmt: skip

    monkeypatch.setattr(
        reshoot, "get_client", lambda: SimpleNamespace(interactions=SimpleNamespace(create=create, get=get))
    )
    yield seen
    load_config.cache_clear()


def test_directed_base_prompt_defers_the_performance_to_the_director(scene: SceneV1) -> None:
    base = reshoot.edit_prompt(scene, reshoot.blockout_palette(scene), directed=True)
    assert "What the people do is described in the PERFORMANCE section below." in base
    assert "Change ONLY the camera" not in base and "Performance (keep EXACTLY" not in base
    assert reshoot.SHOT_RULES_DIRECTED in base and "performance and every event identical" not in base
    plain = reshoot.edit_prompt(scene, reshoot.blockout_palette(scene))
    assert "Change ONLY the camera position, angle, point of view and field of view." in plain


@needs_ffmpeg
def test_directed_reshoot_skips_beats_and_keeps_full_length(
    orig_clip: Path, scene: SceneV1, fake_omni: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from godshot import ingest

    monkeypatch.setattr(reshoot, "performance_beats", lambda *a, **k: pytest.fail("S14 director covers this"))
    out = reshoot.reshoot(orig_clip, scene, scene, tmp_path / "d.mp4", direction=(BRIEF, DIRECTION))
    prompt = fake_omni["input"][-1]["text"]
    assert "PERFORMANCE (" in prompt and DIRECTION.performance_prompt in prompt  # director section appended
    assert "Performance (keep EXACTLY" not in prompt  # no duplicate S12 beats block
    assert prompt.index("Re-render this grey-box") < prompt.index(
        "PERFORMANCE ("
    )  # geometry first (S14 rule)
    meta = json.loads((reshoot.work_dir_for(out) / "reshoot.json").read_text())
    assert meta["directed"] is True and meta["duration_s"] == 10.0 and meta["beats"] == []
    assert ingest.probe(reshoot.work_dir_for(out) / "blockout.mp4").duration_s == pytest.approx(
        10.0, abs=0.15
    )


@needs_ffmpeg
def test_undirected_reshoot_uses_beats(
    orig_clip: Path, scene: SceneV1, fake_omni: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    beat = {"t0": 0.0, "t1": 2.0, "who": "the man in the red hoodie", "action": "waves"}
    monkeypatch.setattr(reshoot, "performance_beats", lambda *a, **k: calls.append(1) or [beat])
    reshoot.reshoot(orig_clip, scene, scene, tmp_path / "u.mp4")
    prompt = fake_omni["input"][-1]["text"]
    assert calls == [1] and "the man in the red hoodie waves" in prompt and "PERFORMANCE (" not in prompt


@pytest.mark.parametrize(("cap", "want"), [(None, 10.0), ("6s", 6.0)])
def test_director_is_told_the_real_length(
    scene: SceneV1, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cap, want
) -> None:
    seen = {}

    def fake_prepare(clip, so, se, out_dir, duration_s=None, request=""):
        seen["duration_s"] = duration_s
        return direct.Pack(BRIEF, DIRECTION)

    def fake_reshoot(clip, so, se, out, take=0, direction=None, **kw):
        out = Path(out)
        reshoot.work_dir_for(out).mkdir(parents=True, exist_ok=True)
        (reshoot.work_dir_for(out) / "raw.mp4").write_bytes(b"raw")
        out.write_bytes(b"v")
        return out

    monkeypatch.setattr(direct, "prepare", fake_prepare)
    monkeypatch.setattr(reshoot, "reshoot", fake_reshoot)
    monkeypatch.setattr(
        reshoot, "conformance", lambda *a, **k: {"score": 1.0, "hits": 1, "usable": 1, "checks": []}
    )
    monkeypatch.setattr(
        direct, "preservation", lambda *a, **k: {"score": 1.0, "kept": 1, "n": 1, "items": []}
    )
    kwargs = {} if cap is None else {"duration": cap}
    reshoot.reshoot_best(Path("c.mp4"), scene, scene, tmp_path, n=1, direct=True, **kwargs)
    assert seen["duration_s"] == want  # no hidden 8 s cut for the director
