"""Run baseline / full over every clip in a split, in parallel. API calls are cached by the modules.

Spec: S11 (.claude/specs/S11*.md) — owner: Siva.

    python eval/run_all.py --split dev --system baseline   # S02 extract only → scene_r0.json
    python eval/run_all.py --split dev --system full       # S06 loop → scene_r0..final, checks, loop_log

Writes eval/runs/<system>/<clip_id>/... (the layout eval/score.py and eval/report.ipynb read).
A clip whose output already exists is skipped unless --force. One failing clip never stops the rest.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

log = logging.getLogger("run_all")

CLIPS_CSV = REPO_ROOT / "eval" / "clips.csv"
RUNS_DIR = REPO_ROOT / "eval" / "runs"
SYSTEMS = ("baseline", "full")
OUTPUT = {"baseline": "scene_r0.json", "full": "scene_final.json"}
WORKERS = 4  # each full run already fans out (8 verify workers); keep the total under Tier-3 limits


def clips_for(split: str, csv_path: Path | None = None) -> list[dict[str, str]]:
    with (csv_path or CLIPS_CSV).open(encoding="utf-8", newline="") as f:
        return [row for row in csv.DictReader(f) if row.get("split") == split]


def _baseline(clip: Path, out_dir: Path) -> None:
    from godshot.extract import baseline  # S02: scene_r0.json + loop_log round 0

    baseline(clip, out_dir)


def _full(clip: Path, out_dir: Path) -> None:
    from godshot.pipeline import run  # S06

    run(clip, out_dir)


RUNNERS: dict[str, Callable[[Path, Path], None]] = {"baseline": _baseline, "full": _full}


def run_one(row: dict[str, str], system: str, runs_dir: Path, force: bool) -> dict[str, Any]:
    clip_id = row["id"]
    clip = REPO_ROOT / row["path"]
    out_dir = runs_dir / system / clip_id
    if not force and (out_dir / OUTPUT[system]).exists():
        return {"clip_id": clip_id, "status": "skipped"}
    if not clip.exists():
        return {"clip_id": clip_id, "status": "missing", "error": f"{row['path']} not found"}
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    try:
        RUNNERS[system](clip, out_dir)
    except Exception as e:  # keep going; report at the end
        log.exception("%s %s failed", system, clip_id)
        return {"clip_id": clip_id, "status": "error", "error": f"{type(e).__name__}: {e}"}
    return {"clip_id": clip_id, "status": "ok", "latency_s": round(time.perf_counter() - t0, 2)}


def main(
    split: str = "test", system: str = "full", force: bool = False, runs_dir: Path = RUNS_DIR
) -> list[dict[str, Any]]:
    if system not in SYSTEMS:
        raise ValueError(f"system must be one of {SYSTEMS}")
    rows = clips_for(split)
    log.info("%s on %d %s clips (%d workers)", system, len(rows), split, WORKERS)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda r: run_one(r, system, runs_dir, force), rows))
    report = runs_dir / system / f"_run_{split}.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return results


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", default="test", choices=["dev", "test"])
    ap.add_argument("--system", default="full", choices=SYSTEMS)
    ap.add_argument("--force", action="store_true", help="re-run clips that already have output")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    results = main(args.split, args.system, args.force)
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"{args.system}/{args.split}: {counts}")
    for r in results:
        if r["status"] in ("error", "missing"):
            print(f"  {r['clip_id']}: {r['status']} {r.get('error', '')}")
    return 0 if not counts.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
