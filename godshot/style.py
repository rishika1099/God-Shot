"""Colors and sizes for the map renderer.

Spec: S07 (.claude/specs/S07-render.md) — owner: Akash.
Sizes are in room units unless the name ends in `_PX` (pixels at the output size).
"""

from __future__ import annotations

# --------------------------------------------------------------------------- colors
BG = "#2b2b2b"
GRID = "#3a3a3a"
ROOM = "#bdbdbd"
CAMERA = "#5ad65a"
TEXT = "#e8e8e8"
TEXT_DIM = "#9a9a9a"
LOWCONF = "#ffc107"  # "?" badge + dashed outline
PROGRESS_BG = "#3a3a3a"
PROGRESS_FG = CAMERA
GHOST = "#8fa8ff"  # the other scene's camera path in before/after views

# Fallback object colors by `type` when the scene's color is the schema default (#888888).
DEFAULT_OBJ_COLOR = "#888888"
OBJ_PALETTE: dict[str, str] = {
    "door": "#6d8b4f",
    "window": "#90caf9",
    "table": "#d7ccc8",
    "chair": "#bcaaa4",
    "sofa": "#7e57c2",
    "couch": "#7e57c2",
    "bed": "#5c6bc0",
    "desk": "#a1887f",
    "shelf": "#8d6e63",
    "car": "#ef5350",
    "tree": "#43a047",
    "plant": "#66bb6a",
}
OBJ_FALLBACK = "#78909c"

# --------------------------------------------------------------------------- geometry (room units)
GRID_STEP = 0.5
FIT_MARGIN = 0.10  # 10% around room + tracks
FOCUS_PAD = 2.5  # action box = camera + actor tracks, padded by this (room units) on every side
ROOM_FIT_MAX_RATIO = 2.0  # include room/objects in the fit only if ≤ this × the padded action area
MIN_FIT_SPAN = 3.0  # never zoom in further than this many units across
FOV_LENGTH = 4.0
CAM_SIZE = 0.35  # side of the camera's rounded square
ACTOR_SHOULDER = (0.55, 0.28)  # ellipse width (across) × depth (front-back)
ACTOR_HEAD_R = 0.13
OBJ_CORNER = 0.08

# --------------------------------------------------------------------------- time
CAM_TRAIL_S = 2.0
ACTOR_TRAIL_S = 1.5
TRAIL_SAMPLES = 16

# --------------------------------------------------------------------------- opacity (0–255)
FOV_ALPHA = 80
FOV_LOWCONF_FACTOR = 0.4  # low-confidence camera → wedge at 40% opacity (+ dashed amber outline)
CAM_PATH_ALPHA = 70  # the camera's whole path, always visible (faint)
TRAIL_ALPHA = 200
GHOST_ALPHA = 110
OBJ_ALPHA = 235

# --------------------------------------------------------------------------- layout (pixels)
HEADER_PX = 72
FOOTER_PX = 28
PROGRESS_H_PX = 6
LABEL_FONT_PX = 30
SMALL_FONT_PX = 18
ROOM_WIDTH_PX = 3
TRAIL_WIDTH_PX = 4
SUPERSAMPLE = 2  # static layer is drawn at 2× and downscaled (antialiasing)
ACTOR_MIN_SHOULDER_PX = 30  # actors never shrink below this on screen (zoomed-out scenes)
BADGE_R_PX = 16
LABEL_PREFIXES = ("actor_", "obj_", "object_")  # stripped from ids for on-map labels
