"""Labeling CLI, spreadsheet → label.v1 JSONL converter, and dataset validator.

Spec: S10 (.claude/specs/S10*.md) — owner: Rishika.

Subcommands:
  template         Generate a CSV labeling sheet for the team 20-minute labeling sprint.
  import-csv       Convert a filled CSV sheet into validated `eval/labels/labels.jsonl`.
  interactive      Extract preview frames at 0.5 s steps and prompt for labels in the terminal.
  fill-entity-map  Link human visual descriptions to `scene_r0.json` entity IDs after baseline runs.
  validate         Validate `labels.jsonl` against `LabelV1` and print split / agreement stats.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from godshot.schema import Label, LabelV1, SceneV1, load_labels, save_labels  # noqa: E402

DEFAULT_LABELS_PATH = REPO_ROOT / "eval" / "labels" / "labels.jsonl"
DEFAULT_CLIPS_CSV = REPO_ROOT / "eval" / "clips.csv"

CSV_COLUMNS = [
    "clip_id",
    "source",
    "license",
    "split",
    "difficulty",
    "labeler",
    "type",
    "t",
    "t0",
    "t1",
    "subject",
    "a",
    "b",
    "value",
]

SAMPLE_ROWS = [
    {
        "clip_id": "tos_001",
        "source": "tears_of_steel",
        "license": "CC BY 3.0",
        "split": "dev",
        "difficulty": "easy",
        "labeler": "Rishika",
        "type": "CAM_TRANSLATION",
        "t": "",
        "t0": "0.0",
        "t1": "4.0",
        "subject": "",
        "a": "",
        "b": "",
        "value": "dolly_in",
    },
    {
        "clip_id": "tos_001",
        "source": "tears_of_steel",
        "license": "CC BY 3.0",
        "split": "dev",
        "difficulty": "easy",
        "labeler": "Rishika",
        "type": "SIDE",
        "t": "2.0",
        "t0": "",
        "t1": "",
        "subject": "the woman in the grey jacket",
        "a": "",
        "b": "",
        "value": "left",
    },
    {
        "clip_id": "tos_001",
        "source": "tears_of_steel",
        "license": "CC BY 3.0",
        "split": "dev",
        "difficulty": "easy",
        "labeler": "Rishika",
        "type": "A_LEFT_OF_B",
        "t": "3.0",
        "t0": "",
        "t1": "",
        "subject": "",
        "a": "the woman in the grey jacket",
        "b": "the large wooden door",
        "value": "true",
    },
]


def round_half(val: float) -> float:
    return round(val * 2.0) / 2.0


def _parse_float(raw: str) -> float | None:
    s = raw.strip()
    return round_half(float(s)) if s else None


def _parse_value(label_type: str, raw: str) -> bool | str:
    s = raw.strip()
    if label_type in ("A_LEFT_OF_B", "TOWARD"):
        low = s.lower()
        if low in ("true", "yes", "1", "y"):
            return True
        if low in ("false", "no", "0", "n"):
            return False
        raise ValueError(f"{label_type} value must be boolean (true/false), got {raw!r}")
    return s


def cmd_template(out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in SAMPLE_ROWS:
            writer.writerow(row)
    print(f"Wrote labeling template ({len(SAMPLE_ROWS)} example rows) -> {out_path}")


def csv_to_labels(csv_path: Path) -> list[LabelV1]:
    """Parse a CSV spreadsheet into validated `LabelV1` records (grouped by clip_id + labeler)."""
    grouped: dict[tuple[str, str], dict] = {}
    with csv_path.open(encoding="utf-8", newline="") as f:
        for row_num, row in enumerate(csv.DictReader(f), start=2):
            clip_id = (row.get("clip_id") or "").strip()
            if not clip_id or clip_id.startswith("#"):
                continue
            labeler = (row.get("labeler") or "").strip() or "unknown"
            key = (clip_id, labeler)
            if key not in grouped:
                diff_raw = (row.get("difficulty") or "").strip()
                diff = [d.strip() for d in diff_raw.replace(",", "|").split("|") if d.strip()]
                grouped[key] = {
                    "clip_id": clip_id,
                    "source": (row.get("source") or "").strip(),
                    "license": (row.get("license") or "").strip(),
                    "split": (row.get("split") or "test").strip(),
                    "difficulty": diff,
                    "labeler": labeler,
                    "labels": [],
                }
            ltype = (row.get("type") or "").strip()
            try:
                val = _parse_value(ltype, row.get("value") or "")
                lbl = Label(
                    label_id=f"L{len(grouped[key]['labels']) + 1}",
                    type=ltype,  # type: ignore[arg-type]
                    t=_parse_float(row.get("t") or ""),
                    t0=_parse_float(row.get("t0") or ""),
                    t1=_parse_float(row.get("t1") or ""),
                    subject=(row.get("subject") or "").strip() or None,
                    a=(row.get("a") or "").strip() or None,
                    b=(row.get("b") or "").strip() or None,
                    value=val,
                )
            except Exception as err:
                raise ValueError(f"{csv_path}:{row_num} ({clip_id}): {err}") from err
            grouped[key]["labels"].append(lbl)

    return [LabelV1.model_validate(payload) for payload in grouped.values()]


def cmd_import_csv(csv_path: Path, out_path: Path, *, replace: bool = False) -> None:
    incoming = csv_to_labels(csv_path)
    merged: dict[tuple[str, str], LabelV1] = {}
    if out_path.exists() and not replace:
        for lb in load_labels(out_path):
            merged[(lb.clip_id, lb.labeler)] = lb
    for lb in incoming:
        key = (lb.clip_id, lb.labeler)
        if key in merged and merged[key].entity_map and not lb.entity_map:
            lb = lb.model_copy(update={"entity_map": dict(merged[key].entity_map)})
        merged[key] = lb
    save_labels(merged.values(), out_path)
    print(f"Imported {len(incoming)} clip-labeler records -> {out_path} (total: {len(merged)})")


def cmd_interactive(
    clip_path: Path,
    *,
    clip_id: str,
    source: str,
    license_str: str,
    split: str,
    difficulty: list[str],
    labeler: str,
    out_path: Path,
) -> None:
    """Extract 0.5 s preview frames (optional) and interactively prompt for human labels."""
    preview_dir = REPO_ROOT / "frames" / f"_label_preview_{clip_id}"
    preview_dir.mkdir(parents=True, exist_ok=True)
    if clip_path.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(clip_path),
                "-vf",
                "fps=2,scale=640:-2",
                str(preview_dir / "frame_%03d.jpg"),
            ],
            check=False,
        )
        print(f"Preview frames extracted at 0.5 s intervals -> {preview_dir}")

    print(f"\nLabeling {clip_id} ({source}, split={split}, labeler={labeler}).")
    print("Types: CAM_TRANSLATION, CAM_ROTATION, SIDE, A_LEFT_OF_B, CLOSER, TOWARD (blank type to finish).")
    labels: list[Label] = []
    while True:
        ltype = input(f"\n[L{len(labels) + 1}] type (blank to save): ").strip().upper()
        if not ltype:
            break
        interval = ltype in ("CAM_TRANSLATION", "CAM_ROTATION", "TOWARD")
        t: float | None = None
        t0: float | None = None
        t1: float | None = None
        if interval:
            t0 = round_half(float(input("  t0 (sec): ").strip()))
            t1 = round_half(float(input("  t1 (sec): ").strip()))
        else:
            t = round_half(float(input("  t (sec): ").strip()))
        subject = a = b = None
        if ltype in ("SIDE", "TOWARD"):
            subject = input("  subject description: ").strip()
        elif ltype in ("A_LEFT_OF_B", "CLOSER"):
            a = input("  entity a description: ").strip()
            b = input("  entity b description: ").strip()
        val_raw = input("  value: ").strip()
        try:
            lbl = Label(
                label_id=f"L{len(labels) + 1}",
                type=ltype,  # type: ignore[arg-type]
                t=t,
                t0=t0,
                t1=t1,
                subject=subject,
                a=a,
                b=b,
                value=_parse_value(ltype, val_raw),
            )
            labels.append(lbl)
            print(f"  Added {lbl.label_id}: {lbl.model_dump(exclude_none=True)}")
        except Exception as err:
            print(f"  Invalid label ({err}); try again.")

    record = LabelV1(
        clip_id=clip_id,
        source=source,  # type: ignore[arg-type]
        license=license_str,
        split="dev" if split == "dev" else "test",
        difficulty=difficulty,
        labeler=labeler,
        entity_map={},
        labels=labels,
    )
    existing = list(load_labels(out_path)) if out_path.exists() else []
    existing = [r for r in existing if (r.clip_id, r.labeler) != (clip_id, labeler)]
    existing.append(record)
    save_labels(existing, out_path)
    print(f"\nSaved {len(labels)} labels for {clip_id} ({labeler}) -> {out_path}")


def cmd_fill_entity_map(clip_id: str, scene_path: Path, labels_path: Path) -> None:
    """Interactive helper to map human entity descriptions -> baseline scene entity IDs."""
    scene = SceneV1.load(scene_path)
    records = load_labels(labels_path)
    entities = [*scene.objects, *scene.actors]
    print(f"Entities in {scene_path}:")
    for e in entities:
        print(f"  {e.id:12s} | {e.visual_description}")

    updated = 0
    for rec in records:
        if rec.clip_id != clip_id:
            continue
        descs: list[str] = []
        for lb in rec.labels:
            for d in (lb.subject, lb.a, lb.b):
                if d and d not in descs:
                    descs.append(d)
        emap = dict(rec.entity_map)
        for d in descs:
            cur = emap.get(d, "")
            ans = input(f"  [{rec.labeler}] {d!r} -> entity_id [{cur}]: ").strip()
            if ans:
                emap[d] = ans
        rec.entity_map = emap
        updated += 1

    if updated:
        save_labels(records, labels_path)
        print(f"Updated entity_map on {updated} record(s) in {labels_path}")
    else:
        print(f"No records found for clip_id={clip_id!r} in {labels_path}")


def _inter_annotator_agreement(records: list[LabelV1]) -> tuple[int, float | None]:
    """Compute exact agreement across clips labeled by >=2 distinct labelers."""
    by_clip: dict[str, list[LabelV1]] = defaultdict(list)
    for r in records:
        by_clip[r.clip_id].append(r)

    double_clips = [recs for recs in by_clip.values() if len(recs) >= 2]
    matches = 0
    total = 0
    for recs in double_clips:
        a_map = {(lb.type, lb.t, lb.t0, lb.t1): lb.value for lb in recs[0].labels}
        b_map = {(lb.type, lb.t, lb.t0, lb.t1): lb.value for lb in recs[1].labels}
        for k in set(a_map) & set(b_map):
            total += 1
            if a_map[k] == b_map[k]:
                matches += 1
    return len(double_clips), (matches / total if total else None)


def cmd_validate(labels_path: Path) -> int:
    if not labels_path.exists():
        print(f"FAIL: {labels_path} does not exist")
        return 1
    records = load_labels(labels_path)
    by_split = Counter(r.split for r in records)
    by_source = Counter(r.source for r in records)
    unique_clips = {r.clip_id for r in records}
    own_test = {r.clip_id for r in records if r.split == "test" and r.source != "camerabench"}
    cb_clips = {r.clip_id for r in records if r.source == "camerabench"}
    dev_clips = {r.clip_id for r in records if r.split == "dev"}
    n_double, iaa = _inter_annotator_agreement(records)

    print(f"OK: {len(records)} LabelV1 records ({len(unique_clips)} unique clips) validated.")
    print(f"  dev clips: {len(dev_clips)} (target: 5) | own test clips: {len(own_test)} (target: >=20)")
    print(f"  CameraBench clips: {len(cb_clips)} (target: 40)")
    print(f"  by split: {dict(by_split)} | by source: {dict(by_source)}")
    iaa_str = f"{iaa:.1%}" if iaa is not None else "n/a"
    print(f"  double-labeled clips: {n_double} | exact agreement on aligned items: {iaa_str}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="God-Shot S10 human labeling & validation tool.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_tpl = sub.add_parser("template", help="Generate a CSV labeling spreadsheet template.")
    p_tpl.add_argument("-o", "--out", type=Path, default=REPO_ROOT / "eval" / "labels" / "sheet_template.csv")

    p_imp = sub.add_parser("import-csv", help="Convert CSV spreadsheet into validated labels.jsonl.")
    p_imp.add_argument("csv_path", type=Path)
    p_imp.add_argument("-o", "--out", type=Path, default=DEFAULT_LABELS_PATH)
    p_imp.add_argument("--replace", action="store_true", help="Overwrite instead of merging.")

    p_int = sub.add_parser("interactive", help="Interactively label one video clip.")
    p_int.add_argument("clip", type=Path)
    p_int.add_argument("--clip-id", required=True)
    p_int.add_argument("--source", required=True)
    p_int.add_argument("--license", dest="license_str", required=True)
    p_int.add_argument("--split", choices=["dev", "test"], default="dev")
    p_int.add_argument("--difficulty", default="easy")
    p_int.add_argument("--labeler", required=True)
    p_int.add_argument("-o", "--out", type=Path, default=DEFAULT_LABELS_PATH)

    p_map = sub.add_parser("fill-entity-map", help="Map human visual descriptions to baseline scene IDs.")
    p_map.add_argument("clip_id")
    p_map.add_argument("scene_json", type=Path)
    p_map.add_argument("--labels", type=Path, default=DEFAULT_LABELS_PATH)

    p_val = sub.add_parser("validate", help="Validate labels.jsonl against LabelV1.")
    p_val.add_argument("labels", type=Path, nargs="?", default=DEFAULT_LABELS_PATH)

    args = parser.parse_args(argv)
    if args.cmd == "template":
        cmd_template(args.out)
        return 0
    if args.cmd == "import-csv":
        cmd_import_csv(args.csv_path, args.out, replace=args.replace)
        return 0
    if args.cmd == "interactive":
        diffs = [d.strip() for d in args.difficulty.replace(",", "|").split("|") if d.strip()]
        cmd_interactive(
            args.clip,
            clip_id=args.clip_id,
            source=args.source,
            license_str=args.license_str,
            split=args.split,
            difficulty=diffs,
            labeler=args.labeler,
            out_path=args.out,
        )
        return 0
    if args.cmd == "fill-entity-map":
        cmd_fill_entity_map(args.clip_id, args.scene_json, args.labels)
        return 0
    if args.cmd == "validate":
        return cmd_validate(args.labels)
    return 1


if __name__ == "__main__":
    sys.exit(main())
