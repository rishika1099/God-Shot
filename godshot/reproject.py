"""3D RGB-D projection, real 3D MediaPipe pose transfer, and dynamic prop animation for novel-view re-shoots.

Instead of feeding Omni a rigid grey-box blockout that discards the original video's real pixels, body
motion, audio, and prop state changes, this module:
  1. Estimates per-frame depth (Depth Anything V2 Small on MPS/CPU) calibrated to `scene_orig` metric scale,
     and extracts real frame-by-frame 3D human body skeletons (33 joints) via MediaPipe Pose — cached on
     disk per clip so subsequent camera-angle edits render in ~2-3 s.
  2. Renders a clean hybrid 3D novel-view guide video (`reprojected.mp4`) from `scene_edited`'s camera track:
     - Small/medium angle shifts (< 65 deg): dense per-frame RGB-D 3D reprojection of the real video pixels
       over the clean 3D blockout room (preserves exact real pixels, punch speed, and prop impact).
     - Large / reverse angles (>= 65 deg): razor-clean 3D parallax room + grounded real 3D MediaPipe body
       (solid torso, real head turn, articulated arms/fists blended with beat targets when off-screen) +
       articulated 3D props (e.g. hinged laptop whose dark outer lid faces the reverse camera and slams
       flat to 0 deg right under the fist on impact!).
     - Muxes the original audio track from `clip.mp4` for exact audio-visual impact timing.
  3. Visually analyzes the 3D-reprojected novel angle vs the original clip (`analyze_novel_view`) so Omni
     receives an angle-grounded visual brief before generating the photorealistic re-shoot.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import shutil
import subprocess
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from godshot import geometry
from godshot.cache import cached
from godshot.config import get_client, load_config
from godshot.schema import Obj, SceneV1, mmss

log = logging.getLogger(__name__)

DEPTH_MODEL_ID = "depth-anything/Depth-Anything-V2-Small-hf"
POSE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_full/float16/latest/pose_landmarker_full.task"
)
CAM_H = 1.5
NEAR = 0.15

# MediaPipe 33-landmark indices
_NOSE = 0
_L_EAR, _R_EAR = 7, 8
_L_SH, _R_SH = 11, 12
_L_EL, _R_EL = 13, 14
_L_WR, _R_WR = 15, 16
_L_IDX, _R_IDX = 19, 20
_L_HIP, _R_HIP = 23, 24
_L_KNEE, _R_KNEE = 25, 26
_L_ANK, _R_ANK = 27, 28


def available() -> bool:
    """True if optional 3D reprojection deps (torch, transformers, mediapipe, cv2) are installed."""
    try:
        import cv2  # noqa: F401
        import mediapipe  # noqa: F401
        import torch  # noqa: F401
        import transformers  # noqa: F401

        return True
    except ImportError:
        return False


# --------------------------------------------------------------------------- cached depth estimator


_DEPTH_CACHE: dict[str, Any] = {}


def _get_depth_model() -> tuple[Any, Any, str]:
    if "model" in _DEPTH_CACHE:
        return _DEPTH_CACHE["proc"], _DEPTH_CACHE["model"], _DEPTH_CACHE["device"]
    import torch
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation

    device = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    log.info("Loading %s on %s...", DEPTH_MODEL_ID, device)
    proc = AutoImageProcessor.from_pretrained(DEPTH_MODEL_ID)
    model = AutoModelForDepthEstimation.from_pretrained(DEPTH_MODEL_ID).to(device).eval()
    _DEPTH_CACHE.update(proc=proc, model=model, device=device)
    return proc, model, device


def estimate_depth_batch(frames_rgb: Sequence[np.ndarray], batch_size: int = 8) -> list[np.ndarray]:
    """Run Depth Anything V2 Small on RGB uint8 frames → list of float32 disparity maps (larger = closer)."""
    import torch

    proc, model, device = _get_depth_model()
    out: list[np.ndarray] = []
    for i in range(0, len(frames_rgb), batch_size):
        chunk = [Image.fromarray(f) for f in frames_rgb[i : i + batch_size]]
        h, w = frames_rgb[i].shape[:2]
        inputs = proc(images=chunk, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            pred = model(**inputs).predicted_depth
            pred = torch.nn.functional.interpolate(
                pred.unsqueeze(1), size=(h, w), mode="bilinear", align_corners=False
            ).squeeze(1)
        arr = pred.detach().cpu().numpy().astype(np.float32)
        for j in range(arr.shape[0]):
            out.append(arr[j])
    return out


# --------------------------------------------------------------------------- MediaPipe 3D pose extraction


def _pose_model_path() -> Path:
    cfg = load_config()
    p = Path(cfg.CACHE_DIR) / "models" / "pose_landmarker_full.task"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        log.info("Downloading MediaPipe Pose Landmarker model to %s...", p)
        urllib.request.urlretrieve(POSE_MODEL_URL, p)
    return p


def _extract_poses_mediapipe(
    frames_rgb: Sequence[np.ndarray], fps: float
) -> list[dict[str, np.ndarray] | None]:
    """Run MediaPipe Pose on RGB frames → list of {world (33,3), img (33,2), vis (33,)} or None."""
    import mediapipe as mp

    results_out: list[dict[str, np.ndarray] | None] = []
    if hasattr(mp, "solutions") and hasattr(mp.solutions, "pose"):
        with mp.solutions.pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            enable_segmentation=False,
            min_detection_confidence=0.45,
            min_tracking_confidence=0.45,
        ) as pose:
            for fr in frames_rgb:
                res = pose.process(fr)
                if not res.pose_world_landmarks or not res.pose_landmarks:
                    results_out.append(None)
                    continue
                wl = np.array(
                    [[lm.x, lm.y, lm.z] for lm in res.pose_world_landmarks.landmark], dtype=np.float32
                )
                il = np.array([[lm.x, lm.y] for lm in res.pose_landmarks.landmark], dtype=np.float32)
                vis = np.array([lm.visibility for lm in res.pose_landmarks.landmark], dtype=np.float32)
                results_out.append({"world": wl, "img": il, "vis": vis})
        return results_out

    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    model_path = _pose_model_path()
    opts = vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.45,
        min_pose_presence_confidence=0.45,
        min_tracking_confidence=0.45,
        output_segmentation_masks=False,
    )
    with vision.PoseLandmarker.create_from_options(opts) as landmarker:
        for i, fr in enumerate(frames_rgb):
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(fr))
            ts_ms = round(i * 1000.0 / max(fps, 1.0))
            res = landmarker.detect_for_video(mp_img, ts_ms)
            if not res.pose_world_landmarks or not res.pose_landmarks:
                results_out.append(None)
                continue
            wl = np.array([[lm.x, lm.y, lm.z] for lm in res.pose_world_landmarks[0]], dtype=np.float32)
            il = np.array([[lm.x, lm.y] for lm in res.pose_landmarks[0]], dtype=np.float32)
            vis = np.array(
                [getattr(lm, "visibility", 1.0) or 1.0 for lm in res.pose_landmarks[0]], dtype=np.float32
            )
            results_out.append({"world": wl, "img": il, "vis": vis})
    return results_out


def _smooth_poses(
    poses: list[dict[str, np.ndarray] | None], alpha: float = 0.65
) -> list[dict[str, np.ndarray] | None]:
    """Fill short detection drops and lightly EMA-smooth 3D joints while keeping fast impacts crisp."""
    out = list(poses)
    last_valid: dict[str, np.ndarray] | None = None
    gap = 0
    for i, p in enumerate(out):
        if p is not None:
            last_valid = p
            gap = 0
        elif last_valid is not None and gap < 6:
            out[i] = {k: v.copy() for k, v in last_valid.items()}
            gap += 1
    prev_w: np.ndarray | None = None
    for p in out:
        if p is None:
            prev_w = None
            continue
        if prev_w is not None:
            p["world"] = alpha * p["world"] + (1.0 - alpha) * prev_w
        prev_w = p["world"].copy()
    return out


# --------------------------------------------------------------------------- video decode + disk track cache


def _read_video_frames(
    clip: Path, target_fps: int, size: tuple[int, int], t0: float = 0.0, t1: float | None = None
) -> tuple[list[np.ndarray], list[float]]:
    """Decode `clip` at `target_fps` resized to `(w, h)` RGB uint8 via cv2."""
    import cv2

    cap = cv2.VideoCapture(str(clip))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {clip}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_src = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    dur = n_src / src_fps if n_src > 0 else (t1 or 5.0)
    end_t = min(dur, t1) if t1 is not None else dur
    w, h = size
    n_out = max(1, round((end_t - t0) * target_fps))
    wanted_src_idx = [
        min(max(0, n_src - 1), round((t0 + i / target_fps) * src_fps)) for i in range(n_out)
    ]
    needed_set = set(wanted_src_idx)
    decoded: dict[int, np.ndarray] = {}
    max_idx = max(wanted_src_idx, default=0)
    idx = 0
    while idx <= max_idx:
        ok, bgr = cap.read()
        if not ok:
            break
        if idx in needed_set:
            rgb = cv2.cvtColor(cv2.resize(bgr, (w, h), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
            decoded[idx] = rgb
        idx += 1
    cap.release()
    frames: list[np.ndarray] = []
    times: list[float] = []
    last_good: np.ndarray | None = None
    for i, sidx in enumerate(wanted_src_idx):
        fr = decoded.get(sidx, last_good)
        if fr is None:
            fr = np.zeros((h, w, 3), dtype=np.uint8)
        else:
            last_good = fr
        frames.append(fr)
        times.append(t0 + i / target_fps)
    return frames, times


def _cam_basis(pose: tuple[float, float, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    cx, cy, yaw = pose
    r = math.radians(yaw)
    pos = np.array([cx, cy, CAM_H], dtype=np.float32)
    fwd = np.array([-math.sin(r), math.cos(r), 0.0], dtype=np.float32)
    right = np.array([math.cos(r), math.sin(r), 0.0], dtype=np.float32)
    up = np.array([0.0, 0.0, 1.0], dtype=np.float32)
    return pos, right, up, fwd


def _room_depth_bounds(scene: SceneV1, cam_pose: tuple[float, float, float]) -> tuple[float, float]:
    pos, _, _, fwd = _cam_basis(cam_pose)
    pts = scene.room.outline or [(-4, -2), (4, -2), (4, 6), (-4, 6)]
    zs = [float((p[0] - pos[0]) * fwd[0] + (p[1] - pos[1]) * fwd[1]) for p in pts]
    pos_zs = [z for z in zs if z > 0.5]
    z_far = max(pos_zs) if pos_zs else 5.5
    return 0.55, max(2.5, min(14.0, z_far))


def _fit_depth_scale(disp: np.ndarray, scene_orig: SceneV1, t: float) -> tuple[float, float, float]:
    """Fit monotonic affine inverse-depth calibration `inv_z = a * disp_norm + b` against `scene_orig`."""
    h, w = disp.shape
    p02, p98 = float(np.percentile(disp, 2)), float(np.percentile(disp, 98))
    dn = np.clip((disp - p02) / max(p98 - p02, 1e-4), 0.0, 1.0)
    cam = geometry.interp_camera(scene_orig, t)
    pos, _, _, fwd = _cam_basis(cam)
    hfov = scene_orig.camera.hfov_deg
    z_near_def, z_far_def = _room_depth_bounds(scene_orig, cam)

    anchors_d: list[float] = []
    anchors_invz: list[float] = []
    for a in scene_orig.actors:
        ax, ay, _, vis = geometry.interp_actor(a, t)
        if not vis:
            continue
        zc = float((ax - pos[0]) * fwd[0] + (ay - pos[1]) * fwd[1])
        sx = geometry.screen_x(cam, (ax, ay), hfov)
        if zc > 0.35 and 0.08 < sx < 0.92:
            u, v = int(np.clip(sx * w, 4, w - 5)), int(h * 0.45)
            d_val = float(np.median(dn[max(0, v - 12) : min(h, v + 12), max(0, u - 12) : min(w, u + 12)]))
            anchors_d.append(d_val)
            anchors_invz.append(1.0 / zc)
    for o in scene_orig.objects:
        ox, oy = o.center[0], o.center[1]
        zc = float((ox - pos[0]) * fwd[0] + (oy - pos[1]) * fwd[1])
        sx = geometry.screen_x(cam, (ox, oy), hfov)
        if zc > 0.35 and 0.08 < sx < 0.92:
            u, v = int(np.clip(sx * w, 4, w - 5)), int(h * 0.58)
            d_val = float(np.median(dn[max(0, v - 12) : min(h, v + 12), max(0, u - 12) : min(w, u + 12)]))
            anchors_d.append(d_val)
            anchors_invz.append(1.0 / zc)
    anchors_d.append(0.05)
    anchors_invz.append(1.0 / z_far_def)

    if len(anchors_d) >= 2 and (max(anchors_d) - min(anchors_d)) > 0.15:
        xd = np.array(anchors_d, dtype=np.float64)
        yi = np.array(anchors_invz, dtype=np.float64)
        A = np.vstack([xd, np.ones_like(xd)]).T
        a_fit, b_fit = np.linalg.lstsq(A, yi, rcond=None)[0]
        if a_fit > 0.05 and b_fit > 0.02:
            return float(a_fit), float(b_fit), z_far_def + 1.5

    inv_far, inv_near = 1.0 / z_far_def, 1.0 / max(0.6, z_near_def)
    return float(inv_near - inv_far), float(inv_far), z_far_def + 1.5


def _disp_to_metric_depth(disp: np.ndarray, a: float, b: float, z_max: float) -> np.ndarray:
    p02, p98 = float(np.percentile(disp, 2)), float(np.percentile(disp, 98))
    dn = np.clip((disp - p02) / max(p98 - p02, 1e-4), 0.0, 1.0)
    return np.clip(1.0 / np.maximum(a * dn + b, 1e-3), 0.35, z_max).astype(np.float32)


def _depth_edge_mask(z_map: np.ndarray, rel_thresh: float = 0.07) -> np.ndarray:
    gx = np.abs(np.diff(z_map, axis=1, prepend=z_map[:, :1]))
    gy = np.abs(np.diff(z_map, axis=0, prepend=z_map[:1, :]))
    return (np.maximum(gx, gy) / np.maximum(z_map, 0.3)) < rel_thresh


def _load_or_extract_3d_tracks(
    clip: Path,
    scene_orig: SceneV1,
    dec_fps: int,
    size: tuple[int, int],
    t0: float,
    t1: float,
) -> tuple[list[np.ndarray], list[float], list[np.ndarray], list[dict[str, np.ndarray] | None]]:
    """Decode frames + run (or load cached) Depth Anything V2 metric depth & MediaPipe 3D pose tracks."""
    from godshot import ingest

    src_frames, src_times = _read_video_frames(clip, dec_fps, size, t0=t0, t1=t1)
    cfg = load_config()
    key_str = json.dumps(
        [ingest.sha256_file(clip), dec_fps, list(size), round(t0, 3), round(t1, 3), "reproj_tracks.v1"]
    )
    digest = hashlib.sha256(key_str.encode()).hexdigest()[:16]
    cache_file = Path(cfg.CACHE_DIR) / "reproject" / f"{digest}.npz"
    if cache_file.exists():
        try:
            z = np.load(cache_file)
            depths_arr = z["depths"]
            world_arr = z["pose_world"]
            img_arr = z["pose_img"]
            vis_arr = z["pose_vis"]
            valid_arr = z["pose_valid"]
            if len(depths_arr) == len(src_frames):
                poses: list[dict[str, np.ndarray] | None] = [
                    {"world": world_arr[i], "img": img_arr[i], "vis": vis_arr[i]} if valid_arr[i] else None
                    for i in range(len(src_frames))
                ]
                log.info(
                    "Loaded cached 3D depth + pose tracks (%d frames) from %s",
                    len(src_frames),
                    cache_file.name,
                )
                return src_frames, src_times, [depths_arr[i] for i in range(len(src_frames))], poses
        except Exception as e:
            log.warning("3D track cache read failed (%s); recomputing", e)

    log.info("3D reprojection: %d frames at %d fps; running Depth Anything V2 + MediaPipe Pose...",
             len(src_frames), dec_fps)
    disps = estimate_depth_batch(src_frames, batch_size=8)
    raw_cal = [_fit_depth_scale(d, scene_orig, t) for d, t in zip(disps, src_times, strict=True)]
    a_med = float(np.median([c[0] for c in raw_cal]))
    b_med = float(np.median([c[1] for c in raw_cal]))
    z_max = float(np.max([c[2] for c in raw_cal]))
    depths_metric = [_disp_to_metric_depth(d, a_med, b_med, z_max) for d in disps]
    poses = _smooth_poses(_extract_poses_mediapipe(src_frames, fps=float(dec_fps)))

    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        n = len(src_frames)
        pw = np.zeros((n, 33, 3), dtype=np.float32)
        pi = np.zeros((n, 33, 2), dtype=np.float32)
        pv = np.zeros((n, 33), dtype=np.float32)
        ok = np.zeros((n,), dtype=np.bool_)
        for i, p in enumerate(poses):
            if p is not None:
                pw[i], pi[i], pv[i], ok[i] = p["world"], p["img"], p["vis"], True
        np.savez_compressed(
            cache_file,
            depths=np.stack(depths_metric, axis=0).astype(np.float16),
            pose_world=pw,
            pose_img=pi,
            pose_vis=pv,
            pose_valid=ok,
        )
    except Exception as e:
        log.warning("3D track cache save failed: %s", e)
    return src_frames, src_times, depths_metric, poses


# --------------------------------------------------------------------------- clean single-frame RGB-D warp


def _unproject_pixels(
    rgb: np.ndarray,
    z_map: np.ndarray,
    cam_pose: tuple[float, float, float],
    hfov_deg: float,
    valid_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    h, w = z_map.shape
    f = (w / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    ys = np.arange(0, h, dtype=np.float32)
    xs = np.arange(0, w, dtype=np.float32)
    uu, vv = np.meshgrid(xs, ys)
    mask = z_map > NEAR
    if valid_mask is not None:
        mask = mask & valid_mask
    uu, vv, zz, cc = uu[mask], vv[mask], z_map[mask], rgb[mask]
    xc = (uu - w / 2.0) * zz / f
    yc = (h / 2.0 - vv) * zz / f
    pos, right, up, fwd = _cam_basis(cam_pose)
    xyz = (
        pos[None, :]
        + xc[:, None] * right[None, :]
        + yc[:, None] * up[None, :]
        + zz[:, None] * fwd[None, :]
    )
    return xyz.astype(np.float32), cc.astype(np.uint8)


def _splat_points(
    xyz: np.ndarray,
    rgb: np.ndarray,
    cam_pose: tuple[float, float, float],
    hfov_deg: float,
    canvas_rgb: np.ndarray,
    zbuf: np.ndarray,
    radius: int = 2,
) -> None:
    """Vectorized painter's-order Z-buffered splatting onto `canvas_rgb` & `zbuf`."""
    if len(xyz) == 0:
        return
    h, w = zbuf.shape
    f = (w / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    pos, right, up, fwd = _cam_basis(cam_pose)
    d = xyz - pos[None, :]
    zc = d @ fwd
    front = zc > NEAR
    if not np.any(front):
        return
    d, zc, col = d[front], zc[front], rgb[front]
    u = np.rint(w / 2.0 + f * (d @ right) / zc).astype(np.int32)
    v = np.rint(h / 2.0 - f * (d @ up) / zc).astype(np.int32)
    in_b = (u >= 0) & (u < w) & (v >= 0) & (v < h)
    if not np.any(in_b):
        return
    u, v, zc, col = u[in_b], v[in_b], zc[in_b], col[in_b]
    order = np.argsort(-zc)
    u, v, zc, col = u[order], v[order], zc[order], col[order]
    for dy in range(-radius, radius + 1):
        vv = np.clip(v + dy, 0, h - 1)
        for dx in range(-radius, radius + 1):
            if radius >= 2 and abs(dx) + abs(dy) > radius + 1:
                continue
            uu = np.clip(u + dx, 0, w - 1)
            closer = zc < zbuf[vv, uu]
            if np.any(closer):
                zbuf[vv[closer], uu[closer]] = zc[closer]
                canvas_rgb[vv[closer], uu[closer]] = col[closer]


def _fill_small_holes(rgb: np.ndarray, filled_mask: np.ndarray) -> np.ndarray:
    """Inpaint small point-splat gaps cleanly while leaving large disoccluded room areas to the blockout."""
    import cv2

    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    closed = cv2.morphologyEx(filled_mask.astype(np.uint8), cv2.MORPH_CLOSE, k)
    small_holes = ((closed > 0) & (~filled_mask)).astype(np.uint8) * 255
    if not np.any(small_holes):
        return rgb
    return cv2.inpaint(rgb, small_holes, 3, cv2.INPAINT_TELEA)


# --------------------------------------------------------------------------- articulated 3D props (laptop!)


def _impact_time_for_prop(prop: Obj, beats: Sequence[dict[str, Any]] | None) -> float | None:
    """Detect if a punch/slam/smash/close beat strikes this prop, returning impact timestamp."""
    if not beats:
        return None
    p_words = {w for w in (prop.type + " " + prop.visual_description).lower().split() if len(w) >= 4}
    for b in beats:
        act = str(b.get("action", "")).lower()
        if any(
            k in act for k in ("punch", "slam", "smash", "hit", "strike", "flatten", "close", "shut")
        ) and (not p_words or any(w in act for w in p_words)):
            t0, t1 = float(b.get("t0", 0.0)), float(b.get("t1", 0.0))
            return t0 + 0.65 * (t1 - t0)
    return None


def _add_hinged_laptop(
    add_fn: Any,
    obj: Obj,
    scene: SceneV1,
    t: float,
    beats: Sequence[dict[str, Any]] | None,
) -> None:
    """Draw a real 3D hinged laptop (black keyboard base + upright lid with green screen facing the actor
    and dark black outer lid facing behind) that physically SLAMS FLAT at the impact timestamp!"""
    from godshot.reshoot import OBJ_Z, _box_faces

    cx, cy = obj.center[0], obj.center[1]
    w, d = max(obj.size[0], 0.40), max(obj.size[1], 0.30)
    # Rest laptop cleanly ON TOP of any supporting counter/table/desk underneath it!
    z_base = OBJ_Z.get(obj.type.lower(), (0.76, 1.05))[0]
    for sup in scene.objects:
        if sup.id == obj.id:
            continue
        if sup.type.lower() in ("counter", "table", "desk", "island") and (
            abs(cx - sup.center[0]) <= max(sup.size[0], 0.5) * 0.65
            and abs(cy - sup.center[1]) <= max(sup.size[1], 0.5) * 0.65
        ):
            sup_top = OBJ_Z.get(sup.type.lower(), (0.0, 0.90))[1]
            z_base = max(z_base, sup_top + 0.01)
    # Orient the laptop screen so it faces the nearest actor (screen = +fwd toward actor)
    yaw = obj.rotation_deg
    if scene.actors:
        ax, ay, _, _ = geometry.interp_actor(scene.actors[0], t)
        # Forward vector of laptop screen pointing toward actor: (-sin(yaw), cos(yaw)) ~ (ax-cx, ay-cy)
        yaw = math.degrees(math.atan2(-(ax - cx), ay - cy))

    # Keyboard base slab (3cm thick, matte dark grey/black)
    for k, fp in enumerate(_box_faces(cx, cy, w, d, yaw, z_base, z_base + 0.035)):
        add_fn(fp, (44, 46, 52) if k == 4 else (28, 30, 34))

    # Compute lid open angle: 105 deg upright before impact → slams flat to 4 deg over 0.25s on punch!
    t_imp = _impact_time_for_prop(obj, beats)
    lid_deg = 105.0
    if t_imp is not None and t >= t_imp - 0.12:
        f = max(0.0, min(1.0, (t - (t_imp - 0.12)) / 0.26))
        lid_deg = 105.0 * (1.0 - f) + 4.0 * f

    r = math.radians(yaw)
    ux, uy = math.cos(r), math.sin(r)       # right along laptop width
    vx, vy = -math.sin(r), math.cos(r)      # forward toward actor (screen side)
    # Hinge runs along the BACK edge of the base (-d/2 along fwd)
    hx, hy, hz = cx - (d * 0.48) * vx, cy - (d * 0.48) * vy, z_base + 0.035
    # Lid rises from hinge: lid_deg=0 → lies flat forward (+fwd); lid_deg=90 → straight up (+z)
    lr = math.radians(lid_deg)
    lid_len = d * 0.96
    tx = hx + lid_len * math.cos(lr) * vx
    ty = hy + lid_len * math.cos(lr) * vy
    tz = hz + lid_len * math.sin(lr)
    hw = w * 0.48

    # Back of lid (outer matte black casing — visible from 180-deg reverse camera!)
    off_b = 0.014
    bx, by, bz = -math.sin(lr) * vx * off_b, -math.sin(lr) * vy * off_b, math.cos(lr) * off_b
    p0 = (hx - hw * ux + bx, hy - hw * uy + by, hz + bz)
    p1 = (hx + hw * ux + bx, hy + hw * uy + by, hz + bz)
    p2 = (tx + hw * ux + bx, ty + hw * uy + by, tz + bz)
    p3 = (tx - hw * ux + bx, ty - hw * uy + by, tz + bz)
    add_fn([p0, p1, p2, p3], (26, 28, 32), (16, 18, 22))

    # Front of lid (bezel + bright green screen facing the actor — hidden behind black lid from 180°!)
    fx, fy, fz = math.sin(lr) * vx * 0.008, math.sin(lr) * vy * 0.008, -math.cos(lr) * 0.008
    q0 = (hx - hw * 0.90 * ux + fx, hy - hw * 0.90 * uy + fy, hz + fz)
    q1 = (hx + hw * 0.90 * ux + fx, hy + hw * 0.90 * uy + fy, hz + fz)
    q2 = (tx + hw * 0.90 * ux + fx, ty + hw * 0.90 * uy + fy, tz + fz)
    q3 = (tx - hw * 0.90 * ux + fx, ty - hw * 0.90 * uy + fy, tz + fz)
    add_fn([q3, q2, q1, q0], (34, 214, 52), (24, 26, 30))


# --------------------------------------------------------------------------- grounded 3D MediaPipe human body


def _pose_to_world_joints(
    pose: dict[str, np.ndarray],
    scene_orig: SceneV1,
    scene_edited: SceneV1,
    actor_idx: int,
    t: float,
    beats: Sequence[dict[str, Any]] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Map MediaPipe 33 3D joints into grounded `scene_edited` world coordinates (33, 3).

    Anchors at shoulder height (`1.38m`, always visible even on waist-up clips), clamps extreme hip
    extrapolation, grounds legs down to `z=0`, and blends beat-driven arm targets whenever MediaPipe
    loses sight of an off-screen arm/wrist during a peak punch or cover-face beat.
    """
    from godshot.reshoot import _actor_arm_pose

    wl = pose["world"].copy()
    vis = pose["vis"].copy()
    cam_o = geometry.interp_camera(scene_orig, min(t, scene_orig.clip.duration_s))
    _, right_o, up_o, fwd_o = _cam_basis(cam_o)

    # Transform relative to mid-shoulder so waist-up framing NEVER makes the torso float!
    mid_sh_mp = 0.5 * (wl[_L_SH] + wl[_R_SH])
    rel = wl - mid_sh_mp[None, :]
    v = (
        rel[:, 0:1] * right_o[None, :]
        + rel[:, 2:3] * fwd_o[None, :]
        + (-rel[:, 1:2]) * up_o[None, :]
    )

    a_edit = scene_edited.actors[actor_idx]
    ax, ay, f_edit, _ = geometry.interp_actor(a_edit, t)
    if actor_idx < len(scene_orig.actors):
        _, _, f_orig, _ = geometry.interp_actor(
            scene_orig.actors[actor_idx], min(t, scene_orig.clip.duration_s)
        )
        df = geometry.wrap180(f_edit - f_orig)
        if abs(df) > 1.0:
            rad = math.radians(df)
            c, s = math.cos(rad), math.sin(rad)
            vx, vy = v[:, 0].copy(), v[:, 1].copy()
            v[:, 0] = vx * c - vy * s
            v[:, 1] = vx * s + vy * c

    # Normalize human scale cleanly via shoulder width (~0.40m)
    sh_w = float(np.linalg.norm(v[_R_SH] - v[_L_SH]))
    scale = float(np.clip(0.42 / max(sh_w, 0.18), 0.75, 1.35))
    v = v * scale

    # Clamp torso lean so waist-up MediaPipe hallucinations don't pitch the person horizontally
    hip_mid = 0.5 * (v[_L_HIP] + v[_R_HIP])
    if hip_mid[2] > -0.32:
        # Synthesize clean grounded hips below shoulders
        r = math.radians(f_edit)
        ux, uy = math.cos(r), math.sin(r)
        v[_L_HIP] = np.array([-0.15 * ux, -0.15 * uy, -0.50], dtype=np.float32)
        v[_R_HIP] = np.array([0.15 * ux, 0.15 * uy, -0.50], dtype=np.float32)

    # Blend beat-driven 3D arm targets when an arm/wrist goes off-screen (low vis) OR during punch wind-up
    arm_pose = _actor_arm_pose(a_edit.visual_description, t, beats)
    if arm_pose is not None:
        re, rh, le, lh = arm_pose
        r = math.radians(f_edit)
        ux, uy = math.cos(r), math.sin(r)
        fx, fy = -math.sin(r), math.cos(r)

        def to_off(o: tuple[float, float, float]) -> np.ndarray:
            return np.array([o[0] * ux + o[1] * fx, o[0] * uy + o[1] * fy, o[2]], dtype=np.float32)

        for el_i, wr_i, idx_i, e_t, h_t in (
            (_R_EL, _R_WR, _R_IDX, to_off(re), to_off(rh)),
            (_L_EL, _L_WR, _L_IDX, to_off(le), to_off(lh)),
        ):
            # If MediaPipe couldn't clearly see the wrist (off-screen wind-up!), trust the beat arm
            w_vis = float(vis[wr_i])
            blend = 0.85 if w_vis < 0.45 else 0.35
            v[el_i] = (1.0 - blend) * v[el_i] + blend * e_t
            v[wr_i] = (1.0 - blend) * v[wr_i] + blend * h_t
            arm_len = max(float(np.linalg.norm(v[wr_i] - v[el_i])), 0.1)
            v[idx_i] = v[wr_i] + 0.08 * (v[wr_i] - v[el_i]) / arm_len
            vis[el_i] = max(float(vis[el_i]), 0.8)
            vis[wr_i] = max(float(vis[wr_i]), 0.8)

    # Anchor mid-shoulder cleanly at 1.38m (slight downward dip when punching/leaning)
    sh_z = 1.38
    world = v + np.array([ax, ay, sh_z], dtype=np.float32)[None, :]

    # Always ground knees & ankles solidly to the floor (z=0) so the actor NEVER floats!
    for hp_i, kn_i, an_i in ((_L_HIP, _L_KNEE, _L_ANK), (_R_HIP, _R_KNEE, _R_ANK)):
        if vis[kn_i] < 0.4 or world[kn_i, 2] > world[hp_i, 2] - 0.15:
            world[kn_i] = np.array([world[hp_i, 0], world[hp_i, 1], 0.46], dtype=np.float32)
            vis[kn_i] = 0.8
        if vis[an_i] < 0.4 or world[an_i, 2] > 0.25:
            world[an_i] = np.array([world[hp_i, 0], world[hp_i, 1], 0.02], dtype=np.float32)
            vis[an_i] = 0.8
    return world, vis


def _render_clean_3d_pose_frame(
    scene_orig: SceneV1,
    scene_edited: SceneV1,
    t: float,
    w: int,
    h: int,
    colors: dict[str, str] | None,
    pose: dict[str, np.ndarray] | None,
    beats: Sequence[dict[str, Any]] | None,
) -> Image.Image:
    """Render clean 3D parallax room + hinged dynamic props (e.g. collapsing laptop!) + grounded real
    3D MediaPipe articulated human skeleton in a single unified depth-sorted pass."""
    from godshot.reshoot import OBJ_Z, WALL_H, _box_faces, _Cam, _clip_near, _hex, _limb_faces, _shade

    cam_pose = geometry.interp_camera(scene_edited, t)
    cam = _Cam(cam_pose, scene_edited.camera.hfov_deg, w, h)
    img = Image.new("RGB", (w, h), (28, 30, 34))
    draw = ImageDraw.Draw(img)
    walls: list[tuple[float, list[tuple[float, float]], tuple[int, int, int], tuple[int, int, int]]] = []
    polys: list[tuple[float, list[tuple[float, float]], tuple[int, int, int], tuple[int, int, int]]] = []

    def add(world_poly, fill, outline=None, layer=None) -> None:
        cpoly = _clip_near([cam.to_cam(p) for p in world_poly])
        if len(cpoly) >= 3:
            depth = sum(c[2] for c in cpoly) / len(cpoly)
            target = polys if layer is None else layer
            target.append((depth, [cam.project(c) for c in cpoly], fill, outline or _shade(fill, 0.6)))

    outline = [tuple(p) for p in scene_edited.room.outline] or [(-5, -1), (5, -1), (5, 10), (-5, 10)]
    floor = _clip_near([cam.to_cam((x, y, 0.0)) for x, y in outline])
    if len(floor) >= 3:
        draw.polygon([cam.project(c) for c in floor], fill=(70, 72, 78))
    xs, ys = [p[0] for p in outline], [p[1] for p in outline]
    for gx in range(math.floor(min(xs)), math.ceil(max(xs)) + 1):
        seg = _clip_near([cam.to_cam((gx, min(ys), 0.0)), cam.to_cam((gx, max(ys), 0.0))])
        if len(seg) == 2:
            draw.line([cam.project(c) for c in seg], fill=(92, 95, 102), width=1)
    for gy in range(math.floor(min(ys)), math.ceil(max(ys)) + 1):
        seg = _clip_near([cam.to_cam((min(xs), gy, 0.0)), cam.to_cam((max(xs), gy, 0.0))])
        if len(seg) == 2:
            draw.line([cam.project(c) for c in seg], fill=(92, 95, 102), width=1)

    for w_idx, ((x0, y0), (x1, y1)) in enumerate(zip(outline, outline[1:] + outline[:1], strict=True)):
        seg_len = math.hypot(x1 - x0, y1 - y0)
        bays = max(1, min(12, round(seg_len / 1.2)))
        for b in range(bays):
            f0, f1 = b / bays, (b + 1) / bays
            bx0, by0 = x0 + (x1 - x0) * f0, y0 + (y1 - y0) * f0
            bx1, by1 = x0 + (x1 - x0) * f1, y0 + (y1 - y0) * f1
            tone = (176, 178, 184) if (w_idx + b) % 2 == 0 else (154, 158, 166)
            add(
                [(bx0, by0, 0.0), (bx1, by1, 0.0), (bx1, by1, WALL_H), (bx0, by0, WALL_H)],
                tone,
                (118, 122, 130),
                layer=walls,
            )

    # Objects (with real 3D hinged collapsing laptop when present!)
    for o in scene_edited.objects:
        if "laptop" in o.type.lower() or "laptop" in o.visual_description.lower():
            _add_hinged_laptop(add, o, scene_edited, t, beats)
            continue
        z0, z1 = OBJ_Z.get(o.type.lower(), (0.0, 1.0))
        base = _hex((colors or {}).get(o.id, o.color))
        for k, fp in enumerate(
            _box_faces(
                o.center[0], o.center[1], max(o.size[0], 0.1), max(o.size[1], 0.1), o.rotation_deg, z0, z1
            )
        ):
            add(fp, _shade(base, 1.15 if k == 4 else 0.85 + 0.05 * k))

    # Actors: use real grounded 3D MediaPipe skeleton for primary actor when detected, beat-driven for others
    for idx, a in enumerate(scene_edited.actors):
        ax, ay, facing, visible = geometry.interp_actor(a, t)
        if not visible:
            continue
        body_rgb = _hex((colors or {}).get(a.id, a.color), (231, 76, 60))
        if idx == 0 and pose is not None:
            joints, vis = _pose_to_world_joints(pose, scene_orig, scene_edited, 0, t, beats=beats)

            def pt(j_i: int, _j: np.ndarray = joints) -> tuple[float, float, float]:
                p = _j[j_i]
                return (float(p[0]), float(p[1]), float(p[2]))

            # Solid untwisted torso prism following real MediaPipe spine lean (mid_hp → mid_sh)
            lsh, rsh = joints[_L_SH], joints[_R_SH]
            lhp, rhp = joints[_L_HIP], joints[_R_HIP]
            mid_sh = 0.5 * (lsh + rsh)
            mid_hp = 0.5 * (lhp + rhp)
            rr_t = math.radians(facing)
            u_t = np.array([math.cos(rr_t), math.sin(rr_t), 0.0], dtype=np.float32)
            f_t = np.array([-math.sin(rr_t), math.cos(rr_t), 0.0], dtype=np.float32)
            sh_l, sh_r = mid_sh - 0.22 * u_t, mid_sh + 0.22 * u_t
            hp_l, hp_r = mid_hp - 0.18 * u_t, mid_hp + 0.18 * u_t
            hd = 0.12 * f_t
            fq = [tuple(p + hd) for p in (hp_l, hp_r, sh_r, sh_l)]
            bq = [tuple(p - hd) for p in (hp_l, hp_r, sh_r, sh_l)]
            add(fq, _shade(body_rgb, 1.05))
            add(bq, _shade(body_rgb, 0.65))
            for i in range(4):
                ni = (i + 1) % 4
                add([fq[i], fq[ni], bq[ni], bq[i]], _shade(body_rgb, 0.85))

            # Grounded legs down to z=0
            leg_col = _shade(body_rgb, 0.55)
            for hp_i, kn_i, an_i in ((_L_HIP, _L_KNEE, _L_ANK), (_R_HIP, _R_KNEE, _R_ANK)):
                for fp in _limb_faces(pt(hp_i), pt(kn_i), half=0.08):
                    add(fp, leg_col)
                for fp in _limb_faces(pt(kn_i), pt(an_i), half=0.065):
                    add(fp, leg_col)

            # Articulated arms + forearms + clenched fists
            for sh_i, el_i, wr_i, idx_i in (
                (_L_SH, _L_EL, _L_WR, _L_IDX),
                (_R_SH, _R_EL, _R_WR, _R_IDX),
            ):
                for fp in _limb_faces(pt(sh_i), pt(el_i), half=0.065):
                    add(fp, _shade(body_rgb, 0.94))
                for fp in _limb_faces(pt(el_i), pt(wr_i), half=0.055):
                    add(fp, (224, 188, 154))
                hp = pt(idx_i) if vis[idx_i] > 0.25 else pt(wr_i)
                for fp in _box_faces(hp[0], hp[1], 0.13, 0.13, facing, hp[2] - 0.065, hp[2] + 0.065):
                    add(fp, (240, 204, 170))

            # Head oriented cleanly with distinct dark visor (front) & dark hair cap (back)
            mid_sh = 0.5 * (lsh + rsh)
            hx, hy, hz = float(mid_sh[0]), float(mid_sh[1]), float(mid_sh[2]) + 0.23
            head_sz = 0.25
            head_cols = (
                (52, 40, 32), (210, 176, 146), (238, 206, 174), (210, 176, 146), (52, 40, 32)
            )
            for k, fp in enumerate(_box_faces(hx, hy, head_sz, head_sz, facing, hz - 0.12, hz + 0.14)):
                add(fp, head_cols[k])
            rr = math.radians(facing)
            ux, uy, vx, vy = math.cos(rr), math.sin(rr), -math.sin(rr), math.cos(rr)
            hw, fd = head_sz * 0.32, head_sz * 0.5 + 0.008
            p_l = (hx - hw * ux + fd * vx, hy - hw * uy + fd * vy)
            p_r = (hx + hw * ux + fd * vx, hy + hw * uy + fd * vy)
            add(
                [(*p_r, hz - 0.01), (*p_l, hz - 0.01), (*p_l, hz + 0.06), (*p_r, hz + 0.06)],
                (36, 36, 42),
                (36, 36, 42),
            )
        else:
            # Standard articulated blockout fallback for secondary actors or undetected frames
            from godshot.reshoot import ACTOR_BODY, ACTOR_HEAD, _actor_arm_pose

            bw, bd, bh = ACTOR_BODY
            for k, fp in enumerate(_box_faces(ax, ay, bw, bd, facing, 0.0, bh)):
                add(fp, _shade(body_rgb, (0.62, 0.85, 1.05, 0.85, 1.1)[k]))
            head_cols = (
                (52, 40, 32), (210, 176, 146), (238, 206, 174), (210, 176, 146), (52, 40, 32)
            )
            for k, fp in enumerate(_box_faces(ax, ay, ACTOR_HEAD, ACTOR_HEAD, facing, bh, bh + ACTOR_HEAD)):
                add(fp, head_cols[k])
            rr = math.radians(facing)
            ux, uy, vx, vy = math.cos(rr), math.sin(rr), -math.sin(rr), math.cos(rr)
            hw, fd = ACTOR_HEAD * 0.32, ACTOR_HEAD * 0.5 + 0.008
            z0, z1 = bh + ACTOR_HEAD * 0.48, bh + ACTOR_HEAD * 0.72
            p_l = (ax - hw * ux + fd * vx, ay - hw * uy + fd * vy)
            p_r = (ax + hw * ux + fd * vx, ay + hw * uy + fd * vy)
            add([(*p_r, z0), (*p_l, z0), (*p_l, z1), (*p_r, z1)], (36, 36, 42), (36, 36, 42))
            arm_pose = _actor_arm_pose(a.visual_description, t, beats)
            if arm_pose is not None:
                re, rh, le, lh = arm_pose
                sz = bh - 0.12
                for sx, e_off, h_off in ((bw * 0.52, re, rh), (-bw * 0.52, le, lh)):
                    p_sh = (ax + sx * ux, ay + sx * uy, sz)
                    p_el = (
                        ax + e_off[0] * ux + e_off[1] * vx,
                        ay + e_off[0] * uy + e_off[1] * vy,
                        sz + e_off[2],
                    )
                    p_ha = (
                        ax + h_off[0] * ux + h_off[1] * vx,
                        ay + h_off[0] * uy + h_off[1] * vy,
                        sz + h_off[2],
                    )
                    for fp in _limb_faces(p_sh, p_el, half=0.06):
                        add(fp, _shade(body_rgb, 0.92))
                    for fp in _limb_faces(p_el, p_ha, half=0.052):
                        add(fp, (220, 184, 150))
                    for fp in _box_faces(
                        p_ha[0], p_ha[1], 0.13, 0.13, facing, p_ha[2] - 0.065, p_ha[2] + 0.065
                    ):
                        add(fp, (236, 200, 168))

    for _, pts, fill, line in [*sorted(walls, key=lambda p: -p[0]), *sorted(polys, key=lambda p: -p[0])]:
        draw.polygon(pts, fill=fill, outline=line)
    return img


# --------------------------------------------------------------------------- main hybrid 3D renderer


def render_reprojected_guide(
    clip: Path,
    scene_orig: SceneV1,
    scene_edited: SceneV1,
    out: Path,
    fps: int = 24,
    size: tuple[int, int] = (640, 360),
    t0: float = 0.0,
    t1: float | None = None,
    colors: dict[str, str] | None = None,
    time_scale: float = 1.0,
    beats: Sequence[dict[str, Any]] | None = None,
    mux_audio: bool = True,
) -> Path:
    """Render hybrid 3D RGB-D + grounded MediaPipe 3D pose + hinged prop novel-view guide video."""
    clip = Path(clip)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    t1 = scene_edited.clip.duration_s if t1 is None else t1
    w, h = size
    n_out = max(1, round((t1 - t0) / time_scale * fps))
    sample_times = [t0 + i / fps * time_scale for i in range(n_out)]
    dec_fps = fps if n_out <= 260 else max(12, fps // 2)

    src_frames, _, depths_metric, poses = _load_or_extract_3d_tracks(
        clip, scene_orig, dec_fps, size, t0, t1
    )

    raw_vid = out.with_suffix(".silent.mp4") if mux_audio else out
    proc = subprocess.Popen(
        [
            shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(raw_vid),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )  # fmt: skip
    assert proc.stdin is not None

    hfov_o = scene_orig.camera.hfov_deg
    hfov_e = scene_edited.camera.hfov_deg
    try:
        for t in sample_times:
            sidx = int(np.clip(round((t - t0) * dec_fps), 0, len(src_frames) - 1))
            src_fr = src_frames[sidx]
            src_zm = depths_metric[sidx].astype(np.float32)
            pose = poses[sidx]

            cam_o = geometry.interp_camera(scene_orig, min(t, scene_orig.clip.duration_s))
            cam_e = geometry.interp_camera(scene_edited, t)
            ang_diff = abs(geometry.wrap180(cam_e[2] - cam_o[2]))
            pos_diff = math.hypot(cam_e[0] - cam_o[0], cam_e[1] - cam_o[1])

            # Clean unified 3D room + hinged props + grounded MediaPipe pose canvas
            base_img = _render_clean_3d_pose_frame(
                scene_orig, scene_edited, t, w, h, colors, pose, beats
            )

            if ang_diff <= 50.0 and pos_diff < 2.8:
                # Small-to-medium angle shift: dense single-frame real RGB-D pixel reprojection!
                canvas = np.array(base_img, dtype=np.uint8)
                zbuf = np.full((h, w), 1e6, dtype=np.float32)
                clean = _depth_edge_mask(src_zm, rel_thresh=0.07)
                fg_xyz, fg_rgb = _unproject_pixels(src_fr, src_zm, cam_o, hfov_o, clean)
                _splat_points(fg_xyz, fg_rgb, cam_e, hfov_e, canvas, zbuf, radius=2)
                canvas = _fill_small_holes(canvas, zbuf < 1e5)
                proc.stdin.write(canvas.tobytes())
            else:
                # Large / 180-deg reverse angle: razor-clean 3D pose + hinged prop + parallax room!
                proc.stdin.write(base_img.tobytes())
    finally:
        proc.stdin.close()
        err = proc.stderr.read().decode() if proc.stderr else ""
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg reproject render failed: {err}")

    if mux_audio and raw_vid != out:
        res = subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(raw_vid), "-ss", f"{t0:.3f}", "-i", str(clip),
                "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", "aac", "-shortest", str(out),
            ],
            capture_output=True,
        )  # fmt: skip
        if res.returncode != 0 or not out.exists():
            shutil.copyfile(raw_vid, out)
        raw_vid.unlink(missing_ok=True)
    return out


# --------------------------------------------------------------------------- novel-view scene analysis


NOVEL_VIEW_PROMPT = """You are comparing two rows of film frames at timestamps {labels}:
- TOP row: ORIGINAL camera angle.
- BOTTOM row: 3D-REPROJECTED novel camera angle showing the new camera viewpoint, real 3D body motion,
  and articulated props (notice how a hinged laptop's black back lid faces away from the actor and slams
  flat when punched).

Write a concise 3–4 sentence visual brief telling a video renderer how to turn the BOTTOM 3D-projected
angle into a photorealistic shot that preserves 100% of the TOP row's physical action:
1. Describe what room walls/background objects appear behind the subject from this NEW angle.
2. Describe how key props (e.g. laptop, table, railing) are oriented relative to this NEW camera and how
   they deform/move on impact.
3. Describe the exact peak physical action & timing visible across the sequence (e.g. arm wind-up,
   violent downward fist punch smashing the laptop flat, recoil, hand on forehead).
Keep it under 550 characters, concrete, and strictly photorealistic.
"""


def analyze_novel_view(
    orig_clip: Path,
    reproj_clip: Path,
    work_dir: Path,
    beats: Sequence[dict[str, Any]] | None = None,
    model: str | None = None,
) -> str:
    """Have Gemini visually inspect the 3D-reprojected novel angle vs the original clip."""
    from google.genai import types

    from godshot import ingest
    from godshot.reshoot import _pick_action_times

    orig_clip, reproj_clip = Path(orig_clip), Path(reproj_clip)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        o_info = ingest.probe(orig_clip, check_length=False)
        r_info = ingest.probe(reproj_clip, check_length=False)
    except Exception as e:
        log.warning("novel-view analysis probe failed: %s", e)
        return ""
    times = _pick_action_times(o_info.duration_s, beats=beats, k=5)
    o_last = o_info.duration_s - 1.2 / o_info.fps
    r_last = r_info.duration_s - 1.2 / r_info.fps
    scale = r_info.duration_s / max(o_info.duration_s, 0.1)

    tw, th, hdr = 320, 180, 22
    sheet = Image.new("RGB", (tw * len(times), th * 2 + hdr), (18, 20, 24))
    draw = ImageDraw.Draw(sheet)
    labels: list[str] = []
    for i, t in enumerate(times):
        lbl = mmss(t)[:7]
        labels.append(lbl)
        of = Image.open(ingest.frame_at(orig_clip, min(t, o_last), work_dir / "orig")).resize((tw, th))
        rf = Image.open(ingest.frame_at(reproj_clip, min(t * scale, r_last), work_dir / "reproj")).resize(
            (tw, th)
        )
        sheet.paste(of.convert("RGB"), (i * tw, hdr))
        sheet.paste(rf.convert("RGB"), (i * tw, hdr + th))
        draw.text((i * tw + 8, 4), f"{lbl}  (top=ORIG, bot=3D-PROJ)", fill=(230, 234, 240))
    sheet_path = work_dir / "novel_view_analysis.jpg"
    sheet.save(sheet_path, quality=88)

    cfg = load_config()
    model = model or cfg.MODEL_CHEAP
    prompt = NOVEL_VIEW_PROMPT.format(labels=", ".join(labels))

    def call() -> str:
        resp = get_client().models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=sheet_path.read_bytes(), mime_type="image/jpeg"),
                prompt,
            ],
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_level="LOW"),
            ),
        )
        return (resp.text or "").strip()

    try:
        text = cached(
            [ingest.sha256_file(orig_clip), ingest.sha256_file(reproj_clip), model, "novel_view.v2"],
            call,
            kind="novel_view",
        )
        (work_dir / "novel_view.json").write_text(
            json.dumps({"analysis": text, "sheet": str(sheet_path)}, indent=2) + "\n", encoding="utf-8"
        )
        return text[:700]
    except Exception as e:
        log.warning("novel-view analysis unavailable (%s: %s)", type(e).__name__, e)
        return ""
