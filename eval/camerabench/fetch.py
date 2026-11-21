"""Download a CameraBench subset (HF syCen/CameraBench) and map tags → our enums.

Spec: S10 (.claude/specs/S10*.md) — owner: Rishika.
Dataset license: CC BY 4.0 (numbers only; do NOT show CameraBench clips in the demo).

Orientation convention VERIFIED against syCen/CameraBench test.jsonl (1,071 clips):
  - arc-CW  co-occurs with truck-left (6) + pan-right (9), 0 truck-right / pan-left  -> arc_left
  - arc-CCW co-occurs with truck-right (11) + pan-left (15), 0 truck-left / pan-right -> arc_right
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from godshot.schema import Label, LabelV1, load_labels, save_labels  # noqa: E402

log = logging.getLogger(__name__)
DEFAULT_CLIPS_DIR = REPO_ROOT / "eval" / "clips"
DEFAULT_CLIPS_CSV = REPO_ROOT / "eval" / "clips.csv"
DEFAULT_LABELS_JSONL = REPO_ROOT / "eval" / "labels" / "labels.jsonl"
DEFAULT_CB_LABELS_JSONL = REPO_ROOT / "eval" / "camerabench" / "labels.jsonl"

DATASET_REPO = "syCen/CameraBench"
DATASET_LICENSE = "CC BY 4.0 (syCen/CameraBench; numbers only)"

TRANSLATION_MAP: dict[str, str] = {
    "dolly-in": "dolly_in",
    "dolly-out": "dolly_out",
    "truck-left": "truck_left",
    "truck-right": "truck_right",
    # Verified from CameraBench co-occurrence: arc-CW = truck-left + pan-right (camera arcs to its left)
    "arc-CW": "arc_left",
    # Verified from CameraBench co-occurrence: arc-CCW = truck-right + pan-left (camera arcs to its right)
    "arc-CCW": "arc_right",
}

ROTATION_MAP: dict[str, str] = {
    "pan-left": "pan_left",
    "pan-right": "pan_right",
}

STATIC_TAGS = frozenset({"no-motion", "static"})


def round_half(t: float) -> float:
    return round(t * 2.0) / 2.0


def map_tags(tags: Sequence[str]) -> tuple[list[str], list[str]]:
    """Map CameraBench tag list to `(translations, rotations)` in our scene.v1 enums.

    Ignores tilt, roll, zoom, pedestal, speed, tracking, and shake tags.
    """
    tag_set = set(tags)
    translations: list[str] = []
    # Prefer arc_* over raw truck_* if both are present since arc describes the combined orbit
    for raw_tag in ("arc-CW", "arc-CCW", "dolly-in", "dolly-out", "truck-left", "truck-right"):
        if raw_tag in tag_set:
            mapped = TRANSLATION_MAP[raw_tag]
            if mapped not in translations:
                translations.append(mapped)

    rotations: list[str] = []
    for raw_tag in ("pan-left", "pan-right"):
        if raw_tag in tag_set:
            rotations.append(ROTATION_MAP[raw_tag])

    if not translations and not rotations and (tag_set & STATIC_TAGS):
        translations.append("static")

    return translations, rotations


def map_difficulty(tags: Sequence[str]) -> list[str]:
    tag_set = set(tags)
    diff: list[str] = []
    if tag_set & {"unsteady", "very-unsteady"}:
        diff.append("handheld")
    if "fast-speed" in tag_set:
        diff.append("fast_motion")
    if not diff:
        diff.append("easy")
    return diff


def probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        text=True,
    ).strip()
    return float(out)


def gif_to_mp4(gif_path: Path, mp4_path: Path) -> Path:
    mp4_path.parent.mkdir(parents=True, exist_ok=True)
    if mp4_path.exists():
        return mp4_path
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(gif_path),
            "-movflags",
            "faststart",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            str(mp4_path),
        ],
        check=True,
    )
    return mp4_path


def _merge_clips_csv(csv_path: Path, new_rows: list[dict[str, str]]) -> None:
    fieldnames = ["id", "source", "license", "split", "difficulty", "duration_s", "path"]
    existing: dict[str, dict[str, str]] = {}
    if csv_path.exists():
        with csv_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("id"):
                    existing[row["id"]] = row
    for row in new_rows:
        existing[row["id"]] = row
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in existing.values():
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def _merge_labels_jsonl(jsonl_path: Path, new_labels: list[LabelV1]) -> None:
    existing: dict[tuple[str, str], LabelV1] = {}
    if jsonl_path.exists():
        for lb in load_labels(jsonl_path):
            existing[(lb.clip_id, lb.labeler)] = lb
    for lb in new_labels:
        existing[(lb.clip_id, lb.labeler)] = lb
    save_labels(existing.values(), jsonl_path)


def main(
    n: int = 40,
    seed: int = 0,
    *,
    min_dur: float = 5.0,
    max_dur: float = 15.0,
    clips_dir: Path = DEFAULT_CLIPS_DIR,
    clips_csv: Path = DEFAULT_CLIPS_CSV,
    labels_jsonl: Path = DEFAULT_LABELS_JSONL,
    cb_labels_jsonl: Path = DEFAULT_CB_LABELS_JSONL,
    split: str = "test",
) -> list[LabelV1]:
    """Download `n` CameraBench clips matching duration/tag filters and write LabelV1 JSONL + clips.csv."""
    from huggingface_hub import hf_hub_download

    test_jsonl = Path(hf_hub_download(DATASET_REPO, "test.jsonl", repo_type="dataset"))
    rows = [json.loads(line) for line in test_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]

    # Filter to clips with at least one mapped translation or rotation tag.
    eligible: list[tuple[dict, list[str], list[str]]] = []
    for row in rows:
        translations, rotations = map_tags(row.get("labels", []))
        if translations or rotations:
            eligible.append((row, translations, rotations))

    rng = random.Random(seed)
    rng.shuffle(eligible)
    log.info("CameraBench: %d/%d rows have mapped translation/rotation tags", len(eligible), len(rows))

    selected_labels: list[LabelV1] = []
    csv_rows: list[dict[str, str]] = []
    skipped_duration = 0

    for row, translations, rotations in eligible:
        if len(selected_labels) >= n:
            break
        video_url = row["Video"]
        if "/resolve/main/" not in video_url:
            continue
        rel_path = video_url.split("/resolve/main/", 1)[1]
        gif_local = Path(hf_hub_download(DATASET_REPO, rel_path, repo_type="dataset"))
        try:
            dur = probe_duration(gif_local)
        except Exception as err:
            log.warning("Skipping %s (ffprobe failed: %s)", rel_path, err)
            continue

        if not (min_dur <= dur <= max_dur):
            skipped_duration += 1
            continue

        idx = len(selected_labels) + 1
        clip_id = f"cb_{idx:03d}"
        mp4_path = clips_dir / f"{clip_id}.mp4"
        gif_to_mp4(gif_local, mp4_path)

        t_end = max(0.5, round_half(dur))
        lbls: list[Label] = []
        lid = 1
        if translations:
            lbls.append(
                Label(label_id=f"L{lid}", type="CAM_TRANSLATION", t0=0.0, t1=t_end, value=translations[0])
            )
            lid += 1
        if rotations:
            lbls.append(Label(label_id=f"L{lid}", type="CAM_ROTATION", t0=0.0, t1=t_end, value=rotations[0]))
            lid += 1

        diff = map_difficulty(row.get("labels", []))
        record = LabelV1(
            clip_id=clip_id,
            source="camerabench",
            license=DATASET_LICENSE,
            split="dev" if split == "dev" else "test",
            difficulty=diff,
            labeler="CameraBench",
            entity_map={},
            labels=lbls,
        )
        selected_labels.append(record)
        rel_clip = (
            mp4_path.relative_to(REPO_ROOT).as_posix()
            if mp4_path.is_relative_to(REPO_ROOT)
            else str(mp4_path)
        )
        csv_rows.append(
            {
                "id": clip_id,
                "source": "camerabench",
                "license": DATASET_LICENSE,
                "split": record.split,
                "difficulty": "|".join(diff),
                "duration_s": f"{dur:.2f}",
                "path": rel_clip,
            }
        )
        log.info(
            "Fetched %s (%s, %.1fs) -> %s",
            clip_id,
            Path(rel_path).name,
            dur,
            [(x.type, x.value) for x in lbls],
        )

    save_labels(selected_labels, cb_labels_jsonl)
    _merge_labels_jsonl(labels_jsonl, selected_labels)
    _merge_clips_csv(clips_csv, csv_rows)
    log.info(
        "Done: saved %d CameraBench clips (skipped %d outside [%.1f, %.1f]s) -> %s",
        len(selected_labels),
        skipped_duration,
        min_dur,
        max_dur,
        cb_labels_jsonl,
    )
    return selected_labels


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch a CameraBench subset and map tags to label.v1.")
    parser.add_argument("--n", type=int, default=40, help="Number of clips to sample (default: 40)")
    parser.add_argument("--seed", type=int, default=0, help="Random seed (default: 0)")
    parser.add_argument("--min-dur", type=float, default=5.0, help="Min duration in seconds (default: 5.0)")
    parser.add_argument("--max-dur", type=float, default=15.0, help="Max duration in seconds (default: 15.0)")
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args()
    main(n=args.n, seed=args.seed, min_dur=args.min_dur, max_dur=args.max_dur)
