"""Wan VACE re-shoot backend (offline: fake fal client, no network)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from godshot import wan_vace
from godshot.config import ConfigError, load_config


@pytest.fixture
def cfg_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GODSHOT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GODSHOT_OFFLINE", raising=False)
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.setattr("godshot.config.load_dotenv", lambda *a, **k: False)  # ignore a developer's .env
    load_config.cache_clear()
    yield
    load_config.cache_clear()


class FakeFal:
    def __init__(self) -> None:
        self.uploads: list[Path] = []
        self.calls: list[dict] = []

    def upload_file(self, path, lifecycle=None):
        self.uploads.append(Path(path))
        return f"https://v3.fal.media/files/{Path(path).name}"

    def subscribe(self, application, arguments, client_timeout=None):
        self.calls.append({"application": application, "arguments": arguments})
        return {"video": {"url": "https://v3.fal.media/files/out.mp4"}, "seed": 1}


def _files(tmp_path: Path) -> tuple[Path, list[Path]]:
    guide = tmp_path / "guide.mp4"
    guide.write_bytes(b"guide")
    refs = []
    for i in range(5):
        r = tmp_path / f"ref{i}.jpg"
        r.write_bytes(f"ref{i}".encode())
        refs.append(r)
    return guide, refs


def test_build_arguments_uses_the_guide_as_a_depth_control() -> None:
    a = wan_vace.build_arguments("p", "https://g", ["u1", "u2", "u3", "u4"], "360p", "depth")
    assert a["video_url"] == "https://g" and a["task"] == "depth" and a["preprocess"] is True
    assert a["match_input_num_frames"] and a["match_input_frames_per_second"] and a["enable_auto_downsample"]
    assert a["ref_image_urls"] == ["u1", "u2", "u3"]
    with pytest.raises(ValueError):
        wan_vace.build_arguments("p", "https://g", [], "1080p", "depth")
    with pytest.raises(ValueError):
        wan_vace.build_arguments("p", "https://g", [], "360p", "scribble")


def test_generate_video_uploads_calls_and_caches(
    cfg_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    guide, refs = _files(tmp_path)
    monkeypatch.setattr(wan_vace, "_download", lambda url: b"VIDEO")
    fake = FakeFal()
    out = wan_vace.generate_video(refs, "prompt", tmp_path / "raw.mp4", guide=guide, client=fake)
    assert out.read_bytes() == b"VIDEO"
    assert fake.uploads == [guide, *refs[: wan_vace.MAX_REFS]]
    (call,) = fake.calls
    assert call["application"] == "fal-ai/wan-vace-14b"
    assert call["arguments"]["video_url"].endswith("guide.mp4")

    again = FakeFal()
    wan_vace.generate_video(refs, "prompt", tmp_path / "raw2.mp4", guide=guide, client=again)
    assert again.calls == [] and (tmp_path / "raw2.mp4").read_bytes() == b"VIDEO"  # served from cache


def test_missing_video_in_response_raises(cfg_env, tmp_path: Path) -> None:
    guide, refs = _files(tmp_path)
    fake = FakeFal()
    fake.subscribe = lambda application, arguments, client_timeout=None: {"detail": "nsfw"}
    with pytest.raises(wan_vace.WanError):
        wan_vace.generate_video(refs, "p", tmp_path / "raw.mp4", guide=guide, client=fake)


def test_missing_fal_key_is_a_config_error(cfg_env, tmp_path: Path) -> None:
    guide, refs = _files(tmp_path)
    with pytest.raises(ConfigError, match="FAL_KEY"):
        wan_vace.generate_video(refs, "p", tmp_path / "raw.mp4", guide=guide)


def test_config_never_prints_the_fal_key(cfg_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAL_KEY", "secret-fal-key-123")
    load_config.cache_clear()
    cfg = load_config()
    assert cfg.FAL_KEY == "secret-fal-key-123"
    assert "secret-fal-key-123" not in repr(cfg) and "FAL_KEY=<set>" in repr(cfg)
    assert cfg.RESHOOT_BACKEND == "omni"


def test_download_refuses_non_https() -> None:
    with pytest.raises(wan_vace.WanError):
        wan_vace._download("file:///etc/passwd")


def test_backend_dispatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from godshot import reshoot

    seen = SimpleNamespace(wan=None, omni=None)

    def fake_wan(refs, prompt, out, guide, resolution="360p", client=None):
        seen.wan = (list(refs), prompt, Path(guide), resolution)
        return Path(out)

    monkeypatch.setattr(wan_vace, "generate_video", fake_wan)
    monkeypatch.setattr(reshoot, "generate_video", lambda *a, **k: setattr(seen, "omni", k) or a[2])
    guide, refs = _files(tmp_path)
    reshoot._generate_guided("wan_vace", refs, [b"x"], "p", tmp_path / "r.mp4", guide, "480p")
    assert seen.wan == (refs, "p", guide, "480p") and seen.omni is None
    reshoot._generate_guided("omni", refs, [b"x"], "p", tmp_path / "r.mp4", guide, "360p")
    assert seen.omni["task"] == "edit" and seen.omni["guide"] == b"guide"
