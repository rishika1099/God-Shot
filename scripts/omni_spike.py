"""Omni spike: 1 ref frame + camera instruction → short 360p clip. Log latency + instruction-following.

Spec: S12 (.claude/specs/S12*.md) — owner: Prajesh (spike).

Usage:
    python scripts/omni_spike.py --clip clips/tos_001.mp4 --t 2.0
    python scripts/omni_spike.py --frame path/to/frame.jpg --prompt "..."

Writes out/spike/<name>/: ref.jpg, omni.mp4 (labelled AI-generated), report.json.
Watch omni.mp4 and fill in the go/no-go in .claude/specs/S12-reshoot.md Notes.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Annotated

import typer

from godshot import ingest
from godshot.config import load_config
from godshot.reshoot import AI_LABEL, generate_video

DEFAULT_PROMPT = (
    "Same room, same person, same lighting as the reference image. "
    "The camera slowly arcs to the right around the person, keeping them in frame."
)

app = typer.Typer(add_completion=False)


@app.command()
def main(
    clip: Annotated[Path | None, typer.Option(help="Source clip; a frame is taken at --t.")] = None,
    t: Annotated[float, typer.Option(help="Timestamp (s) of the reference frame in --clip.")] = 2.0,
    frame: Annotated[
        Path | None, typer.Option(help="Use this JPEG as the reference instead of --clip.")
    ] = None,
    prompt: Annotated[str, typer.Option()] = DEFAULT_PROMPT,
    duration: Annotated[str, typer.Option()] = "5s",
    resolution: Annotated[str, typer.Option()] = "360p",
    out_dir: Annotated[Path, typer.Option()] = Path("out/spike"),
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if (clip is None) == (frame is None):
        raise typer.BadParameter("give exactly one of --clip or --frame")

    name = clip.stem if clip else frame.stem  # type: ignore[union-attr]
    run_dir = out_dir / name
    run_dir.mkdir(parents=True, exist_ok=True)
    if clip:
        ingest.probe(clip)  # validates the clip (length limits, readable)
        ref = ingest.frame_at(clip, t, run_dir / "frames")
    else:
        ref = frame  # type: ignore[assignment]
    ref_bytes = Path(ref).read_bytes()
    (run_dir / "ref.jpg").write_bytes(ref_bytes)

    t0 = time.perf_counter()
    video = generate_video(
        [ref_bytes], prompt, run_dir / "omni.mp4", duration=duration, resolution=resolution
    )
    latency = time.perf_counter() - t0  # ~0 s on a cache hit

    report = {
        "model": load_config().MODEL_RESHOOT,
        "source": str(clip or frame),
        "t": t if clip else None,
        "prompt": prompt,
        "duration": duration,
        "resolution": resolution,
        "latency_s": round(latency, 1),
        "video": str(video),
        "label": AI_LABEL,
        "follows_instruction": None,  # fill in after watching: true / false / "partly"
        "same_room_and_person": None,
        "notes": "",
    }
    (run_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    typer.echo(f"{AI_LABEL}: {video}  ({latency:.1f} s)")
    typer.echo(f"Reference frame: {run_dir / 'ref.jpg'}")
    typer.echo(f"Now watch it and fill in follows_instruction in {run_dir / 'report.json'}")


if __name__ == "__main__":
    app()
