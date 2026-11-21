"""Pure numpy projection math. Positive bearing = point is to the camera's LEFT.

Spec: S03 (.claude/specs/S03*.md) — owner: Siva.
Implement per the spec; keep signatures in sync with it.
"""

from __future__ import annotations

def wrap180(a: float) -> float:
    raise NotImplementedError


def interp_camera(scene, t: float) -> tuple[float, float, float]:
    raise NotImplementedError


def interp_actor(actor, t: float) -> tuple[float, float, float, bool]:
    raise NotImplementedError


def bearing_deg(cam: tuple[float, float, float], p: tuple[float, float]) -> float:
    raise NotImplementedError


def in_view(cam, p, hfov: float) -> bool:
    raise NotImplementedError


def screen_x(cam, p, hfov: float) -> float:
    raise NotImplementedError


def distance(cam, p) -> float:
    raise NotImplementedError


def camera_motion(scene, t0: float, t1: float) -> dict:
    raise NotImplementedError
