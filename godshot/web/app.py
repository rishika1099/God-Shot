"""FastAPI app: run, poll, list cached runs, edit, re-render, re-shoot. No Streamlit.

Spec: S09 (.claude/specs/S09*.md) — owner: Siva.

Run folders live in out/<clip_id>/ (S06 layout: scene_r0.json … scene_final.json, checks_rN.json,
loop_log.json, map.mp4, reel.mp4). eval/runs/full/<clip_id>/ is listed too.
Works before S06/S07/S08 land: the browser draws the map itself; render/re-shoot report "pending".
GODSHOT_OFFLINE=1 → read-only cached demo mode (no uploads, no Hydrogen, no Omni).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from godshot.cache import CacheMiss, is_offline
from godshot.edit import EditError, apply_edit, text_edit
from godshot.schema import SceneV1

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).resolve().parent / "static"
OUT_DIR = REPO_ROOT / "out"
EXTRA_RUN_DIRS = [REPO_ROOT / "eval" / "runs" / "full"]
FRAMES_DIR = REPO_ROOT / "frames"
UPLOAD_DIR = REPO_ROOT / "clips" / "uploads"
CLIP_DIRS = [REPO_ROOT / "clips", REPO_ROOT / "eval" / "clips", UPLOAD_DIR]
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9_\-.]+$")

app = FastAPI(title="God-Shot")
JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


# --------------------------------------------------------------------------- run folders


def _safe_id(clip_id: str) -> str:
    if not _ID_RE.match(clip_id) or clip_id.startswith("."):
        raise HTTPException(400, f"bad id {clip_id!r}")
    return clip_id


def _run_dirs() -> list[Path]:
    seen: dict[str, Path] = {}
    for root in [OUT_DIR, *EXTRA_RUN_DIRS]:
        if root.is_dir():
            for d in sorted(root.iterdir()):
                if d.is_dir() and not d.name.startswith((".", "_")) and _has_scene(d):
                    seen.setdefault(d.name, d)  # out/ wins over eval/runs
    return list(seen.values())


def _has_scene(d: Path) -> bool:
    return (d / "scene_final.json").exists() or (d / "scene_r0.json").exists()


def _run_dir(clip_id: str) -> Path:
    _safe_id(clip_id)
    for d in _run_dirs():
        if d.name == clip_id:
            return d
    raise HTTPException(404, f"no run {clip_id!r}")


def _round_files(d: Path, stem: str) -> list[tuple[int, Path]]:
    out = []
    for p in d.glob(f"{stem}_r*.json"):
        m = re.fullmatch(rf"{stem}_r(\d+)\.json", p.name)
        if m:
            out.append((int(m.group(1)), p))
    return sorted(out)


def _url(p: Path) -> str | None:
    """Browser URL for a file under out/ or eval/runs/ or a clip dir, if it exists."""
    if not p.exists():
        return None
    p = p.resolve()
    for prefix, root in (
        ("/out", OUT_DIR),
        ("/runs", REPO_ROOT / "eval" / "runs"),
        ("/clips", REPO_ROOT / "clips"),
        ("/evalclips", REPO_ROOT / "eval" / "clips"),
    ):
        try:
            return f"{prefix}/{p.relative_to(root.resolve()).as_posix()}?v={int(p.stat().st_mtime)}"
        except ValueError:
            continue
    return None


def _find_clip(d: Path, clip_id: str) -> Path | None:
    for cand in [d / "clip.mp4", *(c / f"{clip_id}.mp4" for c in CLIP_DIRS)]:
        if cand.exists():
            return cand
    return None


def _load_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _latest_edit(d: Path) -> Path | None:
    edits = sorted(d.glob("scene_edit_*.json"), key=lambda p: int(p.stem.rsplit("_", 1)[1]))
    return edits[-1] if edits else None


def _run_payload(d: Path) -> dict[str, Any]:
    clip_id = d.name
    final = d / "scene_final.json"
    scene_path = final if final.exists() else d / "scene_r0.json"
    edit = _latest_edit(d)
    clip = _find_clip(d, clip_id)
    return {
        "clip_id": clip_id,
        "mock": (d / "MOCK").exists(),
        "offline": is_offline(),
        "scene": _load_json(scene_path),
        "scene_edit": _load_json(edit) if edit else None,
        "scenes": {n: _load_json(p) for n, p in _round_files(d, "scene")},
        "checks": {n: _load_json(p) for n, p in _round_files(d, "checks")},
        "loop_log": _load_json(d / "loop_log.json") or [],
        "reel_url": _url(d / "reel.mp4"),
        "reel_edit_url": _url(edit.with_name(edit.stem.replace("scene_", "reel_") + ".mp4"))
        if edit
        else None,
        "reshoot_url": _url(d / "reshoot.mp4"),
        "clip_url": _url(clip) if clip else None,
    }


# --------------------------------------------------------------------------- rendering (S07/S08, optional)


def _render(d: Path, scene: SceneV1, ghost: SceneV1 | None, tag: str) -> dict[str, Any]:
    """map + reel via S07/S08 if they are implemented; otherwise say so (the browser map still works)."""
    clip = _find_clip(d, d.name)
    t0 = time.perf_counter()
    try:
        from godshot import compose, render

        map_mp4 = render.render_map(scene, d / f"map_{tag}.mp4", ghost=ghost)
        if clip is None:
            return {"render": "map_only", "map_url": _url(map_mp4), "reel_url": None}
        reel = compose.compose_reel(clip, map_mp4, d / f"reel_{tag}.mp4")
    except NotImplementedError:
        return {
            "render": "pending",
            "reel_url": None,
            "note": "S07/S08 renderer not merged yet; map shown in browser",
        }
    except Exception as e:  # rendering must never lose the user's edit
        log.exception("render failed")
        return {"render": "error", "reel_url": None, "note": f"{type(e).__name__}: {e}"}
    return {"render": "ok", "reel_url": _url(reel), "render_s": round(time.perf_counter() - t0, 2)}


def _next_edit_tag(d: Path) -> str:
    nums = [int(p.stem.rsplit("_", 1)[1]) for p in d.glob("scene_edit_*.json")]
    return f"edit_{max(nums, default=0) + 1}"


def _base_scene(d: Path) -> SceneV1:
    final = d / "scene_final.json"
    return SceneV1.load(final if final.exists() else d / "scene_r0.json")


def _save_edit(d: Path, scene: SceneV1, render: bool) -> dict[str, Any]:
    tag = _next_edit_tag(d)
    scene.save(d / f"scene_{tag}.json")
    out: dict[str, Any] = {
        "saved": f"scene_{tag}.json",
        "scene": json.loads(scene.model_dump_json(by_alias=True)),
    }
    if render:
        out.update(_render(d, scene, ghost=_base_scene(d), tag=tag))
    return out


def _writable() -> None:
    if is_offline():
        raise HTTPException(403, "offline demo mode (GODSHOT_OFFLINE=1) is read-only")


# --------------------------------------------------------------------------- API


@app.get("/")
def index() -> RedirectResponse:
    return RedirectResponse("/static/index.html")


@app.get("/api/status")
def status() -> dict[str, Any]:
    return {"offline": is_offline(), "runs": len(_run_dirs())}


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    out = []
    for d in _run_dirs():
        log_data = _load_json(d / "loop_log.json") or []
        rates = [r.get("pass_rate") for r in log_data if isinstance(r, dict)]
        out.append(
            {
                "clip_id": d.name,
                "mock": (d / "MOCK").exists(),
                "rounds": len(log_data),
                "pass_rates": rates,
                "has_reel": (d / "reel.mp4").exists(),
                "source": d.parent.name,
            }
        )
    return out


@app.get("/api/run/{clip_id}")
def get_run(clip_id: str) -> dict[str, Any]:
    return _run_payload(_run_dir(clip_id))


class EditBody(BaseModel):
    scene: dict[str, Any] | None = None  # full edited scene from the browser
    edit: dict[str, Any] | None = None  # or a single op for apply_edit
    render: bool = True


@app.post("/api/run/{clip_id}/apply")
def apply(clip_id: str, body: EditBody) -> dict[str, Any]:
    """Preview one op (e.g. a move preset) without saving: returns the edited scene."""
    _run_dir(clip_id)
    if body.edit is None:
        raise HTTPException(400, "body needs 'edit'")
    try:
        scene = SceneV1.model_validate(body.scene) if body.scene else _base_scene(_run_dir(clip_id))
        new = apply_edit(scene, body.edit)
    except (EditError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    return {"scene": json.loads(new.model_dump_json(by_alias=True))}


@app.post("/api/run/{clip_id}/edit")
def save_edit(clip_id: str, body: EditBody) -> dict[str, Any]:
    """Save an edited scene (full JSON, or base scene + one op) and re-render map + reel if possible."""
    _writable()
    d = _run_dir(clip_id)
    try:
        scene = SceneV1.model_validate(body.scene) if body.scene else _base_scene(d)
        if body.edit:
            scene = apply_edit(scene, body.edit)
    except (EditError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    return _save_edit(d, scene, body.render)


class TextEditBody(BaseModel):
    instruction: str
    scene: dict[str, Any] | None = None
    render: bool = True


@app.post("/api/run/{clip_id}/text-edit")
def text_edit_route(clip_id: str, body: TextEditBody) -> dict[str, Any]:
    _writable()
    d = _run_dir(clip_id)
    if not body.instruction.strip():
        raise HTTPException(400, "empty instruction")
    scene = SceneV1.model_validate(body.scene) if body.scene else _base_scene(d)
    try:
        new = text_edit(scene, body.instruction)
    except CacheMiss as e:
        raise HTTPException(409, f"not cached and offline: {e}") from e
    return _save_edit(d, new, body.render)


@app.post("/api/run/{clip_id}/reshoot")
def reshoot_route(clip_id: str, body: EditBody) -> dict[str, Any]:
    """S12 hook: generate a re-shoot of the edited scene with Omni (background job)."""
    _writable()
    d = _run_dir(clip_id)
    clip = _find_clip(d, clip_id)
    if clip is None:
        raise HTTPException(404, "original clip not found (needed for reference frames)")
    edited = SceneV1.model_validate(body.scene) if body.scene else _base_scene(d)

    def work(job: dict[str, Any]) -> None:
        from godshot import reproject, reshoot

        use_reproj = reproject.available()
        job["stage"] = (
            "3D projecting novel angle + generating 2 takes + action verification"
            if use_reproj
            else "generating 2 takes + frame-by-frame action verification"
        )
        summary = reshoot.reshoot_best(
            clip,
            _base_scene(d),
            edited,
            d / "reshoot_takes",
            n=2,
            workers=2,
            mode="reproject" if use_reproj else "blockout",
            verify_actions=True,
            action_retries=1,
        )
        out = d / "reshoot.mp4"
        shutil.copyfile(summary["video"], out)
        job["conformance"] = {
            "score": summary["score"],
            "hits": summary["hits"],
            "usable": summary["usable"],
            "best_take": summary["best_take"],
            "action_score": summary.get("action_score"),
            "combined": summary.get("combined"),
        }
        job["reshoot_url"] = _url(out)

    return {"job_id": _start_job("reshoot", clip_id, work)}


# --------------------------------------------------------------------------- jobs (live runs)


def _start_job(kind: str, clip_id: str, work: Any) -> str:
    job_id = uuid.uuid4().hex[:12]
    job: dict[str, Any] = {
        "id": job_id,
        "kind": kind,
        "clip_id": clip_id,
        "status": "running",
        "stage": "queued",
        "started": time.time(),
    }
    with _JOBS_LOCK:
        JOBS[job_id] = job

    def runner() -> None:
        try:
            work(job)
            job["status"] = "done"
            job["stage"] = "done"
        except NotImplementedError as e:
            job["status"], job["error"] = "error", f"not implemented yet: {e or kind}"
        except Exception as e:
            log.exception("%s job %s failed", kind, job_id)
            job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"
            job["trace"] = traceback.format_exc(limit=5)
        job["elapsed_s"] = round(time.time() - job["started"], 1)

    threading.Thread(target=runner, daemon=True, name=f"job-{job_id}").start()
    return job_id


def _stage_from_files(d: Path) -> str:
    if (d / "reel.mp4").exists():
        return "reel ready"
    if (d / "scene_final.json").exists():
        return "rendering reel"
    checks = _round_files(d, "checks")
    if checks:
        return f"verify/correct round {checks[-1][0]}"
    if (d / "scene_r0.json").exists():
        return "baseline extracted, verifying"
    return "uploading + extracting (Argon)"


@app.post("/api/run")
async def start_run(request: Request, name: str = "clip.mp4") -> dict[str, Any]:
    """Upload a clip as the raw request body (JS: `fetch(url, {method: "POST", body: file})`).

    Runs S06 `pipeline.run` in a background job; poll /api/job/{id}.
    """
    _writable()
    stem = re.sub(r"[^A-Za-z0-9_\-]", "_", Path(name).stem)[:40] or "clip"
    clip_id = f"{stem}_{uuid.uuid4().hex[:6]}"
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "clip too large")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    clip = UPLOAD_DIR / f"{clip_id}.mp4"
    clip.write_bytes(data)
    out_dir = OUT_DIR / clip_id

    def work(job: dict[str, Any]) -> None:
        from godshot import pipeline

        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(clip, out_dir / "clip.mp4")
        job["out"] = str(out_dir)
        pipeline.run(clip, out_dir)

    return {"job_id": _start_job("run", clip_id, work), "clip_id": clip_id}


@app.get("/api/job/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    out = {k: v for k, v in job.items() if k != "trace"}
    if job["kind"] == "run" and job.get("out"):
        d = Path(job["out"])
        if job["status"] == "running":
            out["stage"] = _stage_from_files(d)
        out["loop_log"] = _load_json(d / "loop_log.json") or []
    return out


# --------------------------------------------------------------------------- static + files


@app.get("/frame")
def frame(path: str) -> FileResponse:
    """Check thumbnails: `input_ref.paths` entries (frames/<clip>/<ms>.jpg), confined to the repo."""
    p = (REPO_ROOT / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    allowed = [FRAMES_DIR.resolve(), OUT_DIR.resolve(), (REPO_ROOT / "eval" / "runs").resolve()]
    if p.suffix.lower() not in (".jpg", ".jpeg", ".png") or not any(p.is_relative_to(a) for a in allowed):
        raise HTTPException(403, "not a frame")
    if not p.exists():
        raise HTTPException(404, "frame not extracted")
    return FileResponse(p)


app.mount("/static", StaticFiles(directory=STATIC), name="static")
for _prefix, _root in (
    ("/out", OUT_DIR),
    ("/runs", REPO_ROOT / "eval" / "runs"),
    ("/clips", REPO_ROOT / "clips"),
    ("/evalclips", REPO_ROOT / "eval" / "clips"),
):
    _root.mkdir(parents=True, exist_ok=True)
    app.mount(_prefix, StaticFiles(directory=_root), name=_prefix.strip("/"))


# --------------------------------------------------------------------------- mock demo run


def seed_mock_run(out_dir: Path = OUT_DIR) -> Path:
    """out/mock_001 from mocks/ so the UI has something to show before real runs exist. Marked MOCK."""
    from godshot.schema import load_checks
    from godshot.verify import summary

    mocks = REPO_ROOT / "mocks"
    d = out_dir / "mock_001"
    d.mkdir(parents=True, exist_ok=True)
    (d / "MOCK").write_text("Mock data from mocks/ for UI development. Not a real run.\n", encoding="utf-8")
    for name in ("scene_r0.json", "scene_final.json"):
        shutil.copyfile(mocks / "scene_mock.json", d / name)
    shutil.copyfile(mocks / "checks_mock.json", d / "checks_r0.json")
    s = summary(load_checks(mocks / "checks_mock.json"))
    log_r0 = {
        "round": 0,
        "pass_rate": s["pass_rate"],
        "n_checks": s["n"],
        "n_fail": s["fail"],
        "n_uncertain": s["uncertain"],
        "accepted": True,
        "mock": True,
    }
    (d / "loop_log.json").write_text(json.dumps([log_r0], indent=2) + "\n", encoding="utf-8")
    return d
