"""S01: probe, exact-timestamp frames, trim, and cached uploads (no network)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageStat

from godshot import ingest
from godshot.cache import CacheMiss
from godshot.config import load_config

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed"
)

FPS = 24
BITS = 8
# Each frame shows its own frame number N as 8 vertical bars (LSB on the left): white = 1, black = 0.
# (This ffmpeg build has no drawtext, and a bar code can be read back exactly without OCR.)
BARCODE = f"lum='if(mod(floor(N/pow(2,floor(X*{BITS}/W))),2),235,16)'"


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _make_barcode_clip(path: Path, seconds: float) -> Path:
    _ffmpeg(
        "-f", "lavfi", "-i", f"color=c=black:s=320x64:r={FPS}:d={seconds}",
        "-vf", f"format=gray,geq={BARCODE}",
        # long GOP + B-frames so a seek has to decode past keyframes to land on the right frame
        "-c:v", "libx264", "-preset", "ultrafast", "-g", "48", "-bf", "2", "-pix_fmt", "yuv420p", str(path),
    )  # fmt: skip
    return path


def _read_frame_number(jpg: Path) -> int:
    img = Image.open(jpg).convert("L")
    w, h = img.size
    n = 0
    for bit in range(BITS):
        x0, x1 = int((bit + 0.3) * w / BITS), int((bit + 0.7) * w / BITS)
        mean = ImageStat.Stat(img.crop((x0, h // 4, x1, 3 * h // 4))).mean[0]
        n |= (mean > 128) << bit
    return n


@pytest.fixture(scope="session")
def barcode_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    return _make_barcode_clip(tmp_path_factory.mktemp("clips") / "barcode.mp4", seconds=6)


# --------------------------------------------------------------------------- pure helpers


def test_frame_name() -> None:
    assert ingest.frame_name(0) == "000000.jpg"
    assert ingest.frame_name(2.0) == "002000.jpg"
    assert ingest.frame_name(2.3) == "002300.jpg"  # round, not int(): int(2.3*1000) == 2299
    assert ingest.frame_name(12.3456) == "012346.jpg"
    with pytest.raises(ValueError):
        ingest.frame_name(-0.1)


# --------------------------------------------------------------------------- probe


@needs_ffmpeg
def test_probe(barcode_clip: Path) -> None:
    c = ingest.probe(barcode_clip)
    assert c.id == "barcode"
    assert (c.width, c.height) == (320, 64)
    assert c.fps == pytest.approx(FPS)
    assert c.duration_s == pytest.approx(6.0, abs=0.1)


@needs_ffmpeg
def test_probe_length_limits(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    long_clip = tmp_path / "long.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=gray:s=64x64:r=2:d=36", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(long_clip))  # fmt: skip
    with pytest.raises(ingest.ClipTooLongError):
        ingest.probe(long_clip)
    assert ingest.probe(long_clip, check_length=False).duration_s > 35

    mid_clip = tmp_path / "mid.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=gray:s=64x64:r=2:d=16", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(mid_clip))  # fmt: skip
    with caplog.at_level(logging.WARNING, logger="godshot.ingest"):
        ingest.probe(mid_clip)
    assert any("16.0 s" in r.message for r in caplog.records)


def test_probe_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ingest.IngestError):
        ingest.probe(tmp_path / "nope.mp4")


# --------------------------------------------------------------------------- frames


@needs_ffmpeg
@pytest.mark.parametrize("t", [0.0, 0.5, 1.0, 1.75, 2.0, 2.3, 3.04, 4.5, 5.5])
def test_frame_at_is_exact_within_one_frame(barcode_clip: Path, tmp_path: Path, t: float) -> None:
    out = ingest.frame_at(barcode_clip, t, tmp_path)
    assert out.name == ingest.frame_name(t)
    assert abs(_read_frame_number(out) - t * FPS) <= 1


@needs_ffmpeg
def test_frame_at_scales_and_is_idempotent(barcode_clip: Path, tmp_path: Path) -> None:
    out = ingest.frame_at(barcode_clip, 1.0, tmp_path, max_w=160)
    assert Image.open(out).size == (160, 32)
    mtime = out.stat().st_mtime_ns
    assert ingest.frame_at(barcode_clip, 1.0, tmp_path, max_w=160) == out
    assert out.stat().st_mtime_ns == mtime  # skipped, not re-extracted
    assert not list(tmp_path.glob(".tmp-*"))


@needs_ffmpeg
def test_frame_past_end_raises(barcode_clip: Path, tmp_path: Path) -> None:
    with pytest.raises(ingest.IngestError):
        ingest.frame_at(barcode_clip, 60.0, tmp_path)
    assert not list(tmp_path.glob("*.jpg"))


@needs_ffmpeg
def test_frames_at_order_and_duplicates(barcode_clip: Path, tmp_path: Path) -> None:
    ts = [3.0, 0.5, 3.0, 1.5]
    paths = ingest.frames_at(barcode_clip, ts, tmp_path)
    assert [p.name for p in paths] == [ingest.frame_name(t) for t in ts]
    assert [abs(_read_frame_number(p) - t * FPS) <= 1 for p, t in zip(paths, ts, strict=True)] == [True] * 4


@needs_ffmpeg
def test_40_frames_under_5s(tmp_path: Path) -> None:
    clip = tmp_path / "hd.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=24:d=10", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", str(clip))  # fmt: skip
    ts = [0.25 * i for i in range(40)]
    t0 = time.perf_counter()
    paths = ingest.frames_at(clip, ts, tmp_path / "frames")
    elapsed = time.perf_counter() - t0
    assert len(set(paths)) == 40 and all(p.exists() for p in paths)
    assert elapsed < 5.0, f"40 frames took {elapsed:.2f} s"


# --------------------------------------------------------------------------- trim


@needs_ffmpeg
def test_trim(barcode_clip: Path, tmp_path: Path) -> None:
    out = ingest.trim(barcode_clip, 1.0, 2.5, tmp_path / "sub" / "trim.mp4")
    c = ingest.probe(out)
    assert c.duration_s == pytest.approx(1.5, abs=0.1)
    first = ingest.frame_at(out, 0.0, tmp_path / "f")
    assert abs(_read_frame_number(first) - 1.0 * FPS) <= 1
    with pytest.raises(ValueError):
        ingest.trim(barcode_clip, 2.0, 1.0, tmp_path / "bad.mp4")


# --------------------------------------------------------------------------- upload (fake client)


class FakeFiles:
    def __init__(self, states: list[str]) -> None:
        self.states = states
        self.uploads = 0
        self.gets = 0

    def _file(self, state: str) -> SimpleNamespace:
        return SimpleNamespace(
            name="files/abc123",
            uri="https://generativelanguage.googleapis.com/v1beta/files/abc123",
            mime_type="video/mp4",
            state=SimpleNamespace(name=state),
            expiration_time=datetime.now(UTC) + timedelta(hours=48),
            error=None,
        )

    def upload(self, *, file: str) -> SimpleNamespace:
        self.uploads += 1
        return self._file(self.states[0])

    def get(self, *, name: str) -> SimpleNamespace:
        self.gets += 1
        return self._file(self.states[min(self.gets, len(self.states) - 1)])


@pytest.fixture
def tmp_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GODSHOT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GODSHOT_OFFLINE", raising=False)
    load_config.cache_clear()
    yield tmp_path / "cache"
    load_config.cache_clear()


@pytest.fixture
def fake_clip(tmp_path: Path) -> Path:
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"not really a video, but bytes to hash")
    return p


def _install(monkeypatch: pytest.MonkeyPatch, files: FakeFiles) -> None:
    monkeypatch.setattr(ingest, "get_client", lambda: SimpleNamespace(files=files))


def test_upload_polls_until_active_then_hits_cache(
    tmp_cache: Path, fake_clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = FakeFiles(["PROCESSING", "PROCESSING", "ACTIVE"])
    _install(monkeypatch, files)
    uri = ingest.upload(fake_clip, poll_s=0)
    assert uri.endswith("files/abc123")
    assert (files.uploads, files.gets) == (1, 2)

    def boom() -> None:
        raise AssertionError("second upload must not touch the network")

    monkeypatch.setattr(ingest, "get_client", boom)
    assert ingest.upload(fake_clip, poll_s=0) == uri
    rec = json.loads(next((tmp_cache / "uploads").glob("*.json")).read_text())
    assert rec["sha256"] == ingest.sha256_file(fake_clip) and rec["mime_type"] == "video/mp4"


def test_upload_reuploads_when_stale(
    tmp_cache: Path, fake_clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = FakeFiles(["ACTIVE"])
    _install(monkeypatch, files)
    ingest.upload(fake_clip, poll_s=0)
    rec_path = next((tmp_cache / "uploads").glob("*.json"))
    rec = json.loads(rec_path.read_text())
    rec["uploaded_at"] = (datetime.now(UTC) - timedelta(hours=41)).isoformat()
    rec_path.write_text(json.dumps(rec))
    ingest.upload(fake_clip, poll_s=0)
    assert files.uploads == 2


def test_upload_offline(tmp_cache: Path, fake_clip: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GODSHOT_OFFLINE", "1")
    with pytest.raises(CacheMiss):
        ingest.upload(fake_clip)
    monkeypatch.delenv("GODSHOT_OFFLINE")
    files = FakeFiles(["ACTIVE"])
    _install(monkeypatch, files)
    uri = ingest.upload(fake_clip, poll_s=0)
    rec_path = next((tmp_cache / "uploads").glob("*.json"))
    rec = json.loads(rec_path.read_text())
    rec["uploaded_at"] = (datetime.now(UTC) - timedelta(hours=100)).isoformat()
    rec_path.write_text(json.dumps(rec))
    monkeypatch.setenv("GODSHOT_OFFLINE", "1")
    assert ingest.upload(fake_clip) == uri  # stale is fine offline: downstream calls come from cache
    assert files.uploads == 1


def test_upload_failed_and_timeout(tmp_cache: Path, fake_clip: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeFiles(["PROCESSING", "FAILED"]))
    with pytest.raises(ingest.IngestError, match="FAILED"):
        ingest.upload(fake_clip, poll_s=0)
    _install(monkeypatch, FakeFiles(["PROCESSING"]))
    with pytest.raises(ingest.IngestError, match="not ACTIVE"):
        ingest.upload(fake_clip, timeout_s=0, poll_s=0)
