"""ffmpeg: clip over map → 1080×1920 reel; compare layout for re-shoots.

Spec: S08 (.claude/specs/S08-compose.md) — owner: Akash.

One ffmpeg call per reel. The title bar and the credit end card are drawn with Pillow and fed in as
images, because the Homebrew ffmpeg build has no `drawtext` (no freetype). Output: H.264 High,
yuv420p, faststart, at the clip's fps; audio (AAC) only if the source clip has an audio stream.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import textwrap
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

W, H = 1080, 1920
HALF_H = 960
END_CARD_S = 1.5
TITLE_H = 110
AUDIO_FMT = "aformat=sample_rates=48000:channel_layouts=stereo"
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart"]


class ComposeError(RuntimeError):
    """ffmpeg/ffprobe missing or failed."""


# --------------------------------------------------------------------------- probing


@dataclass(frozen=True)
class Media:
    fps: float
    duration: float
    has_audio: bool


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ComposeError(f"{name} not found on PATH. Install ffmpeg (macOS: `brew install ffmpeg`).")
    return path


def probe_media(path: Path) -> Media:
    """fps (video stream), duration and whether there is an audio stream."""
    proc = subprocess.run(
        [_tool("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type,avg_frame_rate:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    if proc.returncode != 0:
        raise ComposeError(f"ffprobe failed on {path}: {proc.stderr.strip()[-300:]}")
    info = json.loads(proc.stdout or "{}")
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ComposeError(f"no video stream in {path}")
    num, _, den = (video.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return Media(
        fps=fps or 24.0,
        duration=float(info.get("format", {}).get("duration") or 0.0),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


# --------------------------------------------------------------------------- overlays (Pillow)


@lru_cache(maxsize=8)
def _font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=px)
    except TypeError:
        return ImageFont.load_default()


def title_png(title: str, out: Path, width: int = W, height: int = TITLE_H) -> Path:
    """Translucent dark bar with the title, overlaid at the top of the clip region."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 150))
    d = ImageDraw.Draw(img)
    text = textwrap.shorten(title, width=48, placeholder="…")
    d.text((40, height / 2), text, fill=(240, 240, 240, 255), font=_font(48), anchor="lm")
    img.save(out)
    return out


def end_card_png(credit: str, out: Path, width: int = W, height: int = H) -> Path:
    """Black card with the credit / license line, centred and wrapped."""
    img = Image.new("RGB", (width, height), (0, 0, 0))
    d = ImageDraw.Draw(img)
    lines = textwrap.wrap(credit, width=36) or [""]
    font, lh = _font(44), 60
    y0 = height / 2 - lh * (len(lines) - 1) / 2
    for i, line in enumerate(lines):
        d.text((width / 2, y0 + i * lh), line, fill=(230, 230, 230), font=font, anchor="mm")
    d.text((width / 2, height - 120), "made with God-Shot", fill=(110, 110, 110), font=_font(30), anchor="mm")
    img.save(out)
    return out


def label_png(text: str, out: Path) -> Path:
    """Small pill label ("ORIGINAL" / "RE-SHOOT") for the compare layout."""
    font = _font(30)
    w = int(ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(text, font=font)) + 36
    img = Image.new("RGBA", (w, 52), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, w - 1, 51], radius=14, fill=(0, 0, 0, 170))
    ImageDraw.Draw(img).text((w / 2, 26), text, fill=(240, 240, 240, 255), font=font, anchor="mm")
    img.save(out)
    return out


# --------------------------------------------------------------------------- filter graph


def _fit(label_in: str, label_out: str, w: int, h: int, fps: float) -> str:
    """Scale to fit inside w×h, letterbox with black, square pixels, constant fps."""
    return (f"[{label_in}]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}[{label_out}]")


def _finish(
    graph: list[str], stacked: str, inputs: list[str], fps: float, audio_src: str | None,
    end_card: Path | None, main_s: float,
) -> tuple[list[str], list[str]]:
    """Append the optional end card (+ silent audio) and return (graph, -map args).

    The source audio is padded/trimmed to exactly `main_s` (the stacked video's length) so the end card
    and everything after it stays in sync.
    """
    if audio_src is not None:
        graph.append(f"[{audio_src}]{AUDIO_FMT},apad,atrim=0:{main_s:.3f}[a0]")
    if end_card is None:
        return graph, ["-map", f"[{stacked}]"] + (["-map", "[a0]"] if audio_src else [])
    n = len(inputs) // 2  # inputs are ["-i", path, ...] pairs → next input index
    inputs += ["-loop", "1", "-framerate", f"{fps}", "-t", f"{END_CARD_S}", "-i", str(end_card)]
    graph.append(f"[{n}:v]scale={W}:{H},setsar=1,fps={fps},format=yuv420p[card]")
    graph.append(f"[{stacked}]format=yuv420p[main]")
    if audio_src is None:
        graph.append("[main][card]concat=n=2:v=1:a=0[outv]")
        return graph, ["-map", "[outv]"]
    inputs += ["-f", "lavfi", "-t", f"{END_CARD_S}", "-i", "anullsrc=r=48000:cl=stereo"]
    graph.append(f"[{n + 1}:a]{AUDIO_FMT}[a1]")
    graph.append("[main][a0][card][a1]concat=n=2:v=1:a=1[outv][outa]")
    return graph, ["-map", "[outv]", "-map", "[outa]"]


def _run(cmd: list[str], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise ComposeError(f"ffmpeg failed ({proc.returncode}): {proc.stderr.strip()[-800:]}")
    log.info("composed %s in %.1f s", out, time.perf_counter() - t0)
    return out


def _cmd(inputs: list[str], graph: list[str], maps: list[str], out: Path, audio: bool) -> list[str]:
    return [
        _tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *inputs,
        "-filter_complex", ";".join(graph), *maps, *X264,
        *(["-c:a", "aac", "-b:a", "160k"] if audio else []), str(out),
    ]  # fmt: skip


# --------------------------------------------------------------------------- public API


def compose_reel(
    clip: Path, map_mp4: Path, out: Path, title: str | None = None, credit: str | None = None
) -> Path:
    """1080×1920 reel: clip letterboxed into the top 1080×960, map below, optional title + end card."""
    clip, map_mp4, out = Path(clip), Path(map_mp4), Path(out)
    src = probe_media(clip)
    main_s = min(src.duration, probe_media(map_mp4).duration)  # vstack shortest=1
    with tempfile.TemporaryDirectory(prefix="godshot-compose-") as tmp:
        inputs = ["-i", str(clip), "-i", str(map_mp4)]
        graph = [
            _fit("0:v", "top", W, HALF_H, src.fps),
            f"[1:v]scale={W}:{HALF_H},setsar=1,fps={src.fps}[bot]",
        ]
        if title:
            inputs += ["-i", str(title_png(title, Path(tmp) / "title.png"))]
            graph.append("[top][2:v]overlay=0:0[toptitled]")
            top = "toptitled"
        else:
            top = "top"
        graph.append(f"[{top}][bot]vstack=inputs=2:shortest=1[stacked]")
        card = end_card_png(credit, Path(tmp) / "card.png") if credit else None
        audio = "0:a" if src.has_audio else None
        graph, maps = _finish(graph, "stacked", inputs, src.fps, audio, card, main_s)
        return _run(_cmd(inputs, graph, maps, out, src.has_audio), out)


def compose_compare(clip_a: Path, clip_b: Path, map_mp4: Path, out: Path) -> Path:
    """Re-shoot layout (S12): original (left) and generated (right) at 540×960 each, map below.

    Ends with the shortest input. Audio comes from the original clip if it has any.
    """
    clip_a, clip_b, map_mp4, out = Path(clip_a), Path(clip_b), Path(map_mp4), Path(out)
    src = probe_media(clip_a)
    main_s = min(src.duration, probe_media(clip_b).duration, probe_media(map_mp4).duration)
    half = W // 2
    with tempfile.TemporaryDirectory(prefix="godshot-compare-") as tmp:
        la, lb = label_png("ORIGINAL", Path(tmp) / "a.png"), label_png("RE-SHOOT", Path(tmp) / "b.png")
        inputs = ["-i", str(clip_a), "-i", str(clip_b), "-i", str(map_mp4), "-i", str(la), "-i", str(lb)]
        graph = [
            _fit("0:v", "a", half, HALF_H, src.fps),
            _fit("1:v", "b", half, HALF_H, src.fps),
            "[a][3:v]overlay=20:20[al]",
            "[b][4:v]overlay=20:20[bl]",
            "[al][bl]hstack=inputs=2:shortest=1[top]",
            f"[2:v]scale={W}:{HALF_H},setsar=1,fps={src.fps}[bot]",
            "[top][bot]vstack=inputs=2:shortest=1[stacked]",
        ]
        audio = "0:a" if src.has_audio else None
        graph, maps = _finish(graph, "stacked", inputs, src.fps, audio, None, main_s)
        return _run(_cmd(inputs, graph, maps, out, src.has_audio), out)
