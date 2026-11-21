"""Blockout spike: does Omni `edit` keep a map-exact camera move from a grey-box guide video?

Spec: S12 (.claude/specs/S12*.md) — owner: Prajesh.

Hand-built scene approximating `tos_spike_01` (Tears of Steel, CC BY 3.0): one man seen from behind,
a golden machine back-left, a dark cabinet back-right. The camera ORBITS 45° to the right around the
man in 5 s (exact numbers in the scene). We render that as a blockout and ask Omni to re-render it as
the real scene from the reference frame.

Usage:
    python scripts/blockout_spike.py            # uses out/spike/tos_spike_01/ref.jpg
Writes out/spike/blockout_arc/{scene_edited.json, blockout.mp4, omni_edit.mp4, report.json}.
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from typing import Annotated

import typer

from godshot.config import load_config
from godshot.reshoot import AI_LABEL, generate_video, render_blockout
from godshot.schema import SceneV1, mmss

SUBJECT = (0.0, 4.5)
RADIUS = 4.5
ARC_DEG = 45.0
DURATION = 5.0
NOTES = f"Edited camera: orbit {ARC_DEG:.0f} deg right around a1, radius {RADIUS}, {DURATION:.0f} s."

EDIT_PROMPT = (
    "Re-render this grey-box previsualization as the real, photographic scene shown in the reference "
    "image. The red figure is the grey-haired man in the plaid shirt, seen from behind; the gold box is "
    "the large golden machine; the dark brown box is the wooden cabinet. Keep the camera motion, timing, "
    "framing and every position EXACTLY as in the input video: the camera orbits to the right around the "
    "man while keeping him centred. One continuous shot, no cuts. Keep his clothing identical to the "
    "reference image."
)


def arc_scene() -> SceneV1:
    keys = []
    for i in range(int(DURATION * 2) + 1):  # keyframe every 0.5 s
        t = i / 2
        th = math.radians(ARC_DEG * t / DURATION)
        x, y = SUBJECT[0] + RADIUS * math.sin(th), SUBJECT[1] - RADIUS * math.cos(th)
        keys.append(
            {
                "t": t,
                "mmss": mmss(t),
                "x": round(x, 4),
                "y": round(y, 4),
                "yaw_deg": round(math.degrees(th), 3),
            }
        )
    return SceneV1.model_validate(
        {
            "schema": "scene.v1",
            "clip": {"id": "blockout_arc", "duration_s": DURATION, "fps": 24, "width": 640, "height": 360},
            "room": {
                "outline": [[-6, -1.5], [6, -1.5], [6, 10], [-6, 10]],
                "notes": "spike room",
                "confidence": 1,
            },
            "objects": [
                {
                    "id": "machine_1",
                    "type": "machine",
                    "visual_description": "the large golden machine",
                    "shape": "rect",
                    "center": [-2.2, 8.0],
                    "size": [2.4, 1.0],
                    "color": "#c9a227",
                    "confidence": 1,
                },
                {
                    "id": "cabinet_1",
                    "type": "cabinet",
                    "visual_description": "the dark wooden cabinet",
                    "shape": "rect",
                    "center": [3.0, 7.6],
                    "size": [1.2, 0.8],
                    "color": "#5b3a1e",
                    "confidence": 1,
                },
            ],
            "actors": [
                {
                    "id": "a1",
                    "visual_description": "the grey-haired man in the plaid shirt",
                    "color": "#c0392b",
                    "track": [
                        {"t": 0, "x": SUBJECT[0], "y": SUBJECT[1], "facing_deg": 0, "visible": True},
                        {"t": DURATION, "x": SUBJECT[0], "y": SUBJECT[1], "facing_deg": 0, "visible": True},
                    ],
                    "confidence": 1,
                },
            ],
            "camera": {
                "hfov_deg": 60,
                "track": keys,
                "motion_segments": [
                    {"t0": 0, "t1": DURATION, "translation": "arc_right", "rotation": "none"}
                ],
                "confidence": 1,
            },
            "model_notes": NOTES,
        }
    )


def main(
    ref: Annotated[Path, typer.Option(help="Reference frame from the real shot.")] = Path(
        "out/spike/tos_spike_01/ref.jpg"
    ),
    out_dir: Annotated[Path, typer.Option()] = Path("out/spike/blockout_arc"),
    task: Annotated[str, typer.Option(help="Omni video_config.task")] = "edit",
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    out_dir.mkdir(parents=True, exist_ok=True)
    scene = arc_scene()
    scene.save(out_dir / "scene_edited.json")
    guide = render_blockout(scene, out_dir / "blockout.mp4")
    t0 = time.perf_counter()
    video = generate_video(
        [ref.read_bytes()], EDIT_PROMPT, out_dir / "omni_edit.mp4", guide=guide.read_bytes(), task=task
    )
    latency = time.perf_counter() - t0
    report = {
        "model": load_config().MODEL_RESHOOT,
        "task": task,
        "ref": str(ref),
        "edit": scene.model_notes,
        "prompt": EDIT_PROMPT,
        "latency_s": round(latency, 1),
        "video": str(video),
        "label": AI_LABEL,
        "follows_blockout_motion": None,  # fill in after watching
        "notes": "",
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    typer.echo(f"blockout: {guide}\n{AI_LABEL}: {video}  ({latency:.1f} s)")


if __name__ == "__main__":
    typer.run(main)
