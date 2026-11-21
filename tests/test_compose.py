"""S08 tests. Synthetic clips via ffmpeg lavfi (needs ffmpeg; skipped otherwise) + pure Pillow overlays."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image, ImageStat

from godshot import compose
from godshot.render import render_map
from godshot.schema import SceneV1

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg")


def _synth(out: Path, seconds: float, size: str = "1280x720", audio: bool = True, fps: int = 24) -> Path:
    src = f"testsrc2=s={size}:d={seconds}:r={fps}"
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)]
    subprocess.run(cmd, check=True)
    return out


def _streams(path: Path) -> list[dict]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,profile,pix_fmt,width,height,duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    return json.loads(out)["streams"]


def _frame(path: Path, t: float, tmp: Path) -> Image.Image:
    png = tmp / f"f_{t:.2f}.png"
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t}", "-i", str(path), "-frames:v", "1", str(png)]
    subprocess.run(cmd, check=True)
    return Image.open(png).convert("RGB")


def _mean(img: Image.Image, box: tuple[int, int, int, int]) -> float:
    return ImageStat.Stat(img.crop(box).convert("L")).mean[0]


@pytest.fixture(scope="module")
def map_mp4(tmp_path_factory) -> Path:
    if not HAVE_FFMPEG:
        pytest.skip("no ffmpeg")
    mock = Path(__file__).resolve().parent.parent / "mocks" / "scene_mock.json"
    return render_map(SceneV1.load(mock), tmp_path_factory.mktemp("map") / "map.mp4")  # 10 s @ 24 fps


# --------------------------------------------------------------------------- reel


@needs_ffmpeg
def test_reel_with_audio_title_and_credit(map_mp4, tmp_path):
    clip = _synth(tmp_path / "clip.mp4", 10)
    t0 = time.perf_counter()
    out = compose.compose_reel(clip, map_mp4, tmp_path / "reel.mp4", title="Dev clip", credit="CC-BY credit")
    assert time.perf_counter() - t0 < 10  # spec: a 10 s reel composes in < 10 s

    v = next(s for s in _streams(out) if s["codec_type"] == "video")
    a = next(s for s in _streams(out) if s["codec_type"] == "audio")
    assert (v["codec_name"], v["pix_fmt"], v["width"], v["height"]) == ("h264", "yuv420p", 1080, 1920)
    assert v["profile"] in ("High", "Main", "Constrained Baseline")  # phone-playable profiles
    assert float(v["duration"]) == pytest.approx(10 + compose.END_CARD_S, abs=0.15)
    # audio in sync through the (silent) end card
    assert float(a["duration"]) == pytest.approx(float(v["duration"]), abs=0.1)

    mid = _frame(out, 5.0, tmp_path)
    assert _mean(mid, (0, 0, 1080, compose.TITLE_H)) < _mean(mid, (0, 300, 1080, 600))  # dark title bar
    card = _frame(out, 10 + compose.END_CARD_S - 0.3, tmp_path)
    assert _mean(card, (0, 0, 1080, 1920)) < 15  # black card ...
    assert card.convert("L").getextrema()[1] > 150  # ... with visible credit text


@needs_ffmpeg
def test_reel_without_audio_or_credit(map_mp4, tmp_path):
    clip = _synth(tmp_path / "clip.mp4", 4, audio=False)
    out = compose.compose_reel(clip, map_mp4, tmp_path / "reel.mp4")
    streams = _streams(out)
    assert [s["codec_type"] for s in streams] == ["video"]
    assert float(streams[0]["duration"]) == pytest.approx(4, abs=0.15)  # vstack shortest=1 (map is 10 s)


@needs_ffmpeg
def test_letterbox_keeps_aspect(map_mp4, tmp_path):
    clip = _synth(tmp_path / "square.mp4", 2, size="720x720", audio=False)
    out = compose.compose_reel(clip, map_mp4, tmp_path / "reel.mp4")
    f = _frame(out, 1.0, tmp_path)
    assert _mean(f, (0, 0, 50, 960)) < 5  # pillarbox bars left/right of a square clip
    assert _mean(f, (1030, 0, 1080, 960)) < 5
    assert _mean(f, (300, 100, 780, 860)) > 30  # the clip itself


@needs_ffmpeg
def test_credit_card_absent_without_credit(map_mp4, tmp_path):
    clip = _synth(tmp_path / "clip.mp4", 2)
    out = compose.compose_reel(clip, map_mp4, tmp_path / "reel.mp4", title="x")
    assert float(_streams(out)[0]["duration"]) == pytest.approx(2, abs=0.15)


# --------------------------------------------------------------------------- compare


@needs_ffmpeg
def test_compare_layout(map_mp4, tmp_path):
    a = _synth(tmp_path / "a.mp4", 3)
    b = _synth(tmp_path / "b.mp4", 2, size="640x360", audio=False)
    out = compose.compose_compare(a, b, map_mp4, tmp_path / "cmp.mp4")
    streams = _streams(out)
    v = next(s for s in streams if s["codec_type"] == "video")
    a_s = next(s for s in streams if s["codec_type"] == "audio")
    assert (v["width"], v["height"], v["pix_fmt"]) == (1080, 1920, "yuv420p")
    assert float(v["duration"]) == pytest.approx(2, abs=0.15)  # shortest input
    assert float(a_s["duration"]) == pytest.approx(2, abs=0.1)
    f = _frame(out, 1.0, tmp_path)
    assert _mean(f, (40, 300, 500, 660)) > 30 and _mean(f, (580, 300, 1040, 660)) > 30  # both halves filled


# --------------------------------------------------------------------------- pure overlays / errors


def test_end_card_png(tmp_path):
    credit = "Tears of Steel (CC-BY 3.0) © Blender Foundation | mango.blender.org"
    img = Image.open(compose.end_card_png(credit, tmp_path / "c.png"))
    assert img.size == (1080, 1920)
    g = img.convert("L")
    assert g.getextrema()[1] > 150 and _mean(img, (0, 0, 1080, 300)) < 2


def test_title_png_is_translucent(tmp_path):
    img = Image.open(compose.title_png("A very long title " * 10, tmp_path / "t.png"))
    assert img.size == (1080, compose.TITLE_H) and img.mode == "RGBA"
    assert img.getpixel((5, 5))[3] == 150


def test_missing_ffmpeg_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(compose.shutil, "which", lambda name: None)
    with pytest.raises(compose.ComposeError, match="not found"):
        compose.compose_reel(tmp_path / "c.mp4", tmp_path / "m.mp4", tmp_path / "o.mp4")
