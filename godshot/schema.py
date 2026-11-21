"""Pydantic models for scene.v1, check.v1, label.v1. FROZEN after S00 (see .claude/docs/contracts.md).

Spec: S00 (.claude/specs/S00*.md) — owner: Prajesh.
Additive OPTIONAL fields only, with a dated note in contracts.md. Never rename or remove fields.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------- helpers


def mmss(t: float) -> str:
    """Seconds → 'MM:SS.sss' (e.g. 62.5 → '01:02.500'). Negative input is clamped to 0."""
    ms_total = max(0, round(t * 1000))
    minutes, ms_rem = divmod(ms_total, 60_000)
    seconds, ms = divmod(ms_rem, 1000)
    return f"{minutes:02d}:{seconds:02d}.{ms:03d}"


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class _Timed(_Model):
    """Mixin: `mmss` is derived from `t` when left empty."""

    t: float = Field(ge=0)
    mmss: str = ""

    @model_validator(mode="after")
    def _fill_mmss(self) -> Self:
        if not self.mmss:
            self.mmss = mmss(self.t)
        return self


def _save_json(model: BaseModel, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(model.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8")


# Fixed-length list (not tuple) so the JSON schema avoids `prefixItems` for Gemini structured output.
Point = Annotated[list[float], Field(min_length=2, max_length=2)]

# --------------------------------------------------------------------------- scene.v1

Translation = Literal[
    "static", "dolly_in", "dolly_out", "truck_left", "truck_right", "arc_left", "arc_right", "handheld_drift"
]
Rotation = Literal["none", "pan_left", "pan_right"]

MAX_OBJECTS = 6
MAX_ACTORS = 3


class Clip(_Model):
    id: str
    duration_s: float = Field(gt=0)
    fps: float = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class Room(_Model):
    outline: list[Point] = Field(default_factory=list)
    notes: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)


class Obj(_Model):
    id: str
    type: str
    visual_description: str
    shape: Literal["rect", "circle"] = "rect"
    center: Point
    size: Point
    rotation_deg: float = 0.0
    color: str = "#888888"
    confidence: float = Field(default=0.5, ge=0, le=1)


class ActorKey(_Timed):
    x: float
    y: float
    facing_deg: float = 0.0
    visible: bool = True


class Actor(_Model):
    id: str
    visual_description: str
    color: str = "#e74c3c"
    track: list[ActorKey] = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0, le=1)


class CamKey(_Timed):
    x: float
    y: float
    yaw_deg: float = 0.0


class MotionSegment(_Model):
    t0: float = Field(ge=0)
    t1: float = Field(ge=0)
    translation: Translation = "static"
    rotation: Rotation = "none"

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.t1 < self.t0:
            raise ValueError(f"motion segment t1 ({self.t1}) < t0 ({self.t0})")
        return self


class Camera(_Model):
    hfov_deg: float = Field(default=60.0, gt=0, lt=180)
    track: list[CamKey] = Field(min_length=1)
    motion_segments: list[MotionSegment] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


class LowConf(_Model):
    entity_id: str
    t0: float
    t1: float
    failed_checks: list[str] = Field(default_factory=list)


class SceneV1(_Model):
    schema_: Literal["scene.v1"] = Field(default="scene.v1", alias="schema")
    clip: Clip
    room: Room = Field(default_factory=Room)
    objects: list[Obj] = Field(default_factory=list, max_length=MAX_OBJECTS)
    actors: list[Actor] = Field(default_factory=list, max_length=MAX_ACTORS)
    camera: Camera
    low_confidence: list[LowConf] = Field(default_factory=list)
    model_notes: str = ""

    @model_validator(mode="after")
    def _unique_entities(self) -> Self:
        entities: list[Obj | Actor] = [*self.objects, *self.actors]
        ids = [e.id for e in entities]
        if len(ids) != len(set(ids)):
            raise ValueError(f"entity ids must be unique, got {ids}")
        descs = [e.visual_description.strip().lower() for e in entities]
        if len(descs) != len(set(descs)):
            raise ValueError("every entity needs a unique visual_description")
        return self

    def entity(self, entity_id: str) -> Obj | Actor:
        for e in [*self.objects, *self.actors]:
            if e.id == entity_id:
                return e
        raise KeyError(entity_id)

    @classmethod
    def load(cls, path: str | Path) -> SceneV1:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> None:
        _save_json(self, path)


# --------------------------------------------------------------------------- check.v1

Template = Literal[
    "VISIBLE", "NOT_VISIBLE", "SIDE_LEFT", "SIDE_RIGHT", "A_LEFT_OF_B", "CLOSER", "TOWARD", "PAN", "DOLLY"
]
Verdict = Literal["pass", "fail", "uncertain"]


class InputRef(_Model):
    kind: Literal["frame", "pair"]
    paths: list[str]

    @model_validator(mode="after")
    def _n_paths(self) -> Self:
        want = 1 if self.kind == "frame" else 2
        if len(self.paths) != want:
            raise ValueError(f"input_ref.kind={self.kind} needs {want} path(s), got {len(self.paths)}")
        return self


class NoulCriteria(_Model):
    true_description: str = ""
    false_description: str = ""


class Question(_Model):
    type: Literal["noul", "choice"]
    instructions: str
    criteria: NoulCriteria | None = None  # noul only
    options: dict[str, str] | None = None  # choice only: {key: full-sentence description}

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.type == "choice" and (not self.options or len(self.options) < 2):
            raise ValueError("choice questions need >= 2 options (include 'other' as the catch-all)")
        if self.type == "noul" and self.options:
            raise ValueError("noul questions must not have options")
        return self

    def to_api(self) -> dict[str, Any]:
        """Wire format for `client.decisions.create(questions=...)` (verified against google-genai 3.6.1).

        On the wire, choice options go in `criteria` as {key: description}; noul uses
        `criteria: {true_description, false_description}`.
        """
        if self.type == "choice":
            return {"type": "choice", "instructions": self.instructions, "criteria": dict(self.options or {})}
        out: dict[str, Any] = {"type": "noul", "instructions": self.instructions}
        if self.criteria and (self.criteria.true_description or self.criteria.false_description):
            out["criteria"] = self.criteria.model_dump(exclude_defaults=True)
        return out


class CheckResult(_Model):
    """RAW verifier answer (scene-independent) + an optional scene-relative verdict.

    p_yes / choice_probs / confidence come straight from the Decisions API and are cached by `qkey`.
    `verdict` = compare(predict(scene, check), observed(raw)) for ONE particular scene (S05/S06);
    it is None when only the raw answer is stored.
    """

    p_yes: float | None = None  # noul: API `noul` field, P(true) in [0.01, 0.99]
    choice_probs: dict[str, float] | None = None  # choice: API `probabilities`
    confidence: float = Field(ge=0, le=1)
    verdict: Verdict | None = None


class CheckV1(_Model):
    check_id: str
    template: Template
    t: float = Field(ge=0)
    mmss: str = ""
    t_end: float | None = None
    input_ref: InputRef
    entities: list[str] = Field(default_factory=list)
    expected: bool | str  # bool for noul, option key for choice
    margin: float | None = None
    question: Question
    result: CheckResult | None = None
    control: bool = False  # mirrored-control side check (S04); optional, added in S00

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not self.mmss:
            self.mmss = mmss(self.t)
        if self.question.type == "noul" and not isinstance(self.expected, bool):
            raise ValueError(f"{self.check_id}: noul check needs a bool `expected`")
        if self.question.type == "choice" and (
            not isinstance(self.expected, str) or self.expected not in (self.question.options or {})
        ):
            raise ValueError(f"{self.check_id}: choice `expected` must be one of the option keys")
        return self


QKey = tuple[tuple[str, ...], str]


def qkey(check: CheckV1) -> QKey:
    """Identity of a QUESTION (not a check): same frames + same wording → same raw answer.

    Shared by S04/S05/S06: the per-clip question bank and the answer cache are keyed by this.
    """
    return (tuple(check.input_ref.paths), check.question.instructions)


def make_check_id(template: str, entities: Sequence[str], t: float) -> str:
    """Stable check id: sha1(template, entities, t)[:8], so checks compare across rounds (S04)."""
    raw = f"{template}|{'+'.join(entities)}|{t:.3f}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


def load_checks(path: str | Path) -> list[CheckV1]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [CheckV1.model_validate(d) for d in data]


def save_checks(checks: Iterable[CheckV1], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = [c.model_dump(mode="json", by_alias=True) for c in checks]
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- label.v1

LabelType = Literal["CAM_TRANSLATION", "CAM_ROTATION", "SIDE", "A_LEFT_OF_B", "CLOSER", "TOWARD"]
Source = Literal["own_footage", "camerabench", "tears_of_steel", "sintel", "spring", "charge", "pexels"]


class Label(_Model):
    label_id: str
    type: LabelType
    t: float | None = None  # instant labels (SIDE, A_LEFT_OF_B, CLOSER)
    t0: float | None = None  # interval labels (CAM_*, TOWARD)
    t1: float | None = None
    subject: str | None = None  # SIDE, TOWARD
    a: str | None = None  # A_LEFT_OF_B, CLOSER
    b: str | None = None
    value: bool | str  # e.g. "dolly_in", "left", True, "a"

    @model_validator(mode="after")
    def _times(self) -> Self:
        interval = self.type in ("CAM_TRANSLATION", "CAM_ROTATION", "TOWARD")
        if interval and (self.t0 is None or self.t1 is None):
            raise ValueError(f"{self.label_id}: {self.type} needs t0 and t1")
        if not interval and self.t is None:
            raise ValueError(f"{self.label_id}: {self.type} needs t")
        return self


class LabelV1(_Model):
    clip_id: str
    source: Source
    license: str
    split: Literal["dev", "test"]
    difficulty: list[str] = Field(default_factory=list)  # easy|handheld|crowd|whip_pan|low_light|long_take
    labeler: str
    entity_map: dict[str, str] = Field(default_factory=dict)  # visual description → scene entity id
    labels: list[Label] = Field(default_factory=list)


def load_labels(path: str | Path) -> list[LabelV1]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [LabelV1.model_validate_json(line) for line in lines if line.strip()]


def save_labels(labels: Iterable[LabelV1], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(lb.model_dump_json() + "\n" for lb in labels), encoding="utf-8")
