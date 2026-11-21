"""S05 verify tests: offline, with answers recorded in mocks/checks_mock.json replayed by a fake client."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from godshot import verify as v
from godshot.cache import CacheMiss
from godshot.config import load_config
from godshot.schema import CheckResult, CheckV1, Question, SceneV1, qkey

# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def tmp_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GODSHOT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GODSHOT_OFFLINE", raising=False)
    monkeypatch.setattr(v, "RETRY_BACKOFF_S", 0.0)
    load_config.cache_clear()
    yield tmp_path / "cache"
    load_config.cache_clear()


def _jpeg(path: Path, w: int = 64, h: int = 36, color: tuple[int, int, int] = (90, 120, 150)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), color).save(path, format="JPEG")


@pytest.fixture
def frames_dir(tmp_path: Path, checks_mock: list[CheckV1]) -> Path:
    """Tiny distinct JPEGs at every path the mock checks reference (relative to frames_dir)."""
    root = tmp_path / "frames_root"
    paths = sorted({p for c in checks_mock for p in c.input_ref.paths})
    for i, p in enumerate(paths):
        _jpeg(root / p, color=(10 * i, 100, 200 - 10 * i))
    return root


class _Err(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status


class FakeClient:
    """Stands in for genai.Client: `client.decisions.create(...)` answers from recorded results."""

    def __init__(
        self, recorded: dict[tuple, CheckResult | None], fail_first: list[int] | None = None
    ) -> None:
        self.recorded = recorded  # (context text, instructions) → raw
        self.calls: list[dict[str, Any]] = []
        self.fail_first = list(fail_first or [])
        self.decisions = SimpleNamespace(create=self._create)

    def _create(self, *, model: str, input: list, questions: dict) -> Any:
        self.calls.append({"model": model, "input": input, "questions": questions})
        if self.fail_first:
            raise _Err(self.fail_first.pop(0))
        answers = {}
        for qid, q in questions.items():
            raw = self.recorded[(input[0]["text"], q["instructions"])]
            if raw.p_yes is not None:
                answers[qid] = SimpleNamespace(type="noul", noul=raw.p_yes, confidence=raw.confidence)
            else:
                answers[qid] = SimpleNamespace(
                    type="choice",
                    probabilities=raw.choice_probs,
                    choice=max(raw.choice_probs, key=raw.choice_probs.get),
                    confidence=raw.confidence,
                )
        return SimpleNamespace(answers=answers)


@pytest.fixture
def fake(checks_mock: list[CheckV1]) -> FakeClient:
    return FakeClient(_recorded(checks_mock))


def _recorded(checks: list[CheckV1]) -> dict[tuple, CheckResult]:
    return {(v.check_context(c), c.question.instructions): c.result for c in checks}


def _expected_predict(_scene: SceneV1, c: CheckV1) -> bool | str | None:
    """Stub for S04 predict(): the scene that proposed a check predicts its `expected`."""
    return c.expected


# --------------------------------------------------------------------------- observed / compare


@pytest.mark.parametrize(
    ("p", "want"),
    [(0.99, True), (0.7, True), (0.69, None), (0.5, None), (0.31, None), (0.3, False), (0.01, False)],
)
def test_observed_noul_thresholds(tmp_cache: Path, p: float, want: bool | None) -> None:
    assert v.observed({"p_yes": p, "confidence": 0.5}) is want


def test_observed_choice(tmp_cache: Path) -> None:
    assert v.observed(
        {"choice_probs": {"pan_left": 0.6, "pan_right": 0.3, "other": 0.1}, "confidence": 0.6}
    ) == ("pan_left")
    assert v.observed({"choice_probs": {"pan_left": 0.5, "other": 0.5}, "confidence": 0.5}) in (
        "pan_left",
        None,
    )
    assert (
        v.observed({"choice_probs": {"pan_left": 0.2, "pan_right": 0.1, "other": 0.7}, "confidence": 0.7})
        is None
    )
    assert (
        v.observed({"choice_probs": {"a_first": 0.49, "b_second": 0.4, "other": 0.11}, "confidence": 0.49})
        is None
    )
    assert v.observed(None) is None


def test_observed_uses_config_thresholds(tmp_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GODSHOT_PASS_THRESHOLD", "0.9")
    load_config.cache_clear()
    assert v.observed({"p_yes": 0.8, "confidence": 0.8}) is None


@pytest.mark.parametrize(
    ("pred", "obs", "want"),
    [
        (True, True, "pass"),
        (False, False, "pass"),
        (True, False, "fail"),
        ("pan_left", "pan_left", "pass"),
        ("pan_left", "pan_right", "fail"),
        (None, True, "fail"),  # abstaining scene vs confident observation
        (None, "dolly_in", "fail"),
        (True, None, "uncertain"),
        (None, None, "uncertain"),
    ],
)
def test_compare(pred: object, obs: object, want: str) -> None:
    assert v.compare(pred, obs) == want


# --------------------------------------------------------------------------- verify (batched, cached)


def test_verify_one_request_per_input_and_reproduces_mock_verdicts(
    tmp_cache: Path, frames_dir: Path, fake: FakeClient, checks_mock: list[CheckV1], scene_mock: SceneV1
) -> None:
    answers = v.verify(checks_mock, frames_dir, client=fake)
    distinct_inputs = {tuple(c.input_ref.paths) for c in checks_mock}
    assert len(fake.calls) == len(distinct_inputs)
    assert len(answers) == len({qkey(c) for c in checks_mock})
    assert all(a.verdict is None for a in answers.values())  # raw only

    call = fake.calls[0]
    assert call["model"].startswith("models/")
    assert call["input"][0]["type"] == "text"
    img = call["input"][1]
    assert img["type"] == "image" and img["mime_type"] == "image/jpeg"
    Image.open(io.BytesIO(base64.b64decode(img["data"]))).verify()

    attached = v.attach(scene_mock, checks_mock, answers, predict=_expected_predict)
    for got, want in zip(attached, checks_mock, strict=True):
        assert got.result is not None
        assert got.result.verdict == want.result.verdict, got.check_id
        assert got.result.p_yes == want.result.p_yes


def test_pair_checks_batch_together(
    tmp_cache: Path, frames_dir: Path, fake: FakeClient, checks_mock: list[CheckV1]
) -> None:
    pair = [c for c in checks_mock if c.template in ("TOWARD", "DOLLY")]  # both on frames 0 s + 4 s
    v.verify(pair, frames_dir, client=fake)
    assert len(fake.calls) == 1
    assert len(fake.calls[0]["questions"]) == 2
    assert sum(1 for b in fake.calls[0]["input"] if b["type"] == "image") == 2
    assert "Image 1 is at 00:00.000, Image 2 is at 00:04.000" in fake.calls[0]["input"][0]["text"]


def test_second_verify_makes_zero_calls(
    tmp_cache: Path, frames_dir: Path, fake: FakeClient, checks_mock: list[CheckV1]
) -> None:
    first = v.verify(checks_mock, frames_dir, client=fake)
    n = len(fake.calls)
    second = v.verify(checks_mock, frames_dir, client=fake)
    assert len(fake.calls) == n
    assert second == first


def test_shared_qkey_asked_once(
    tmp_cache: Path, frames_dir: Path, fake: FakeClient, checks_mock: list[CheckV1]
) -> None:
    c = checks_mock[0]
    twin = c.model_copy(update={"check_id": "twin0001", "result": None})
    answers = v.verify([c, twin], frames_dir, client=fake)
    assert len(fake.calls) == 1
    assert len(fake.calls[0]["questions"]) == 1
    assert list(answers) == [qkey(c)]


def test_partial_cache_only_asks_new_questions(
    tmp_cache: Path, frames_dir: Path, fake: FakeClient, checks_mock: list[CheckV1]
) -> None:
    same_frame = [c for c in checks_mock if c.input_ref.paths == ["frames/mock_001/001000.jpg"]]
    assert len(same_frame) >= 3
    v.verify(same_frame[:1], frames_dir, client=fake)
    v.verify(same_frame, frames_dir, client=fake)
    assert len(fake.calls) == 2
    assert len(fake.calls[1]["questions"]) == len(same_frame) - 1


def test_offline_cache_miss_raises(
    tmp_cache: Path,
    frames_dir: Path,
    fake: FakeClient,
    checks_mock: list[CheckV1],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GODSHOT_OFFLINE", "1")
    with pytest.raises(CacheMiss):
        v.verify(checks_mock[:1], frames_dir, client=fake)
    assert fake.calls == []


def test_offline_hit_works(
    tmp_cache: Path,
    frames_dir: Path,
    fake: FakeClient,
    checks_mock: list[CheckV1],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    online = v.verify(checks_mock, frames_dir, client=fake)
    monkeypatch.setenv("GODSHOT_OFFLINE", "1")
    assert v.verify(checks_mock, frames_dir, client=FakeClient({})) == online


def test_retry_once_on_429(tmp_cache: Path, frames_dir: Path, checks_mock: list[CheckV1]) -> None:
    fake = FakeClient(_recorded(checks_mock), fail_first=[429])
    answers = v.verify(checks_mock[:1], frames_dir, client=fake)
    assert len(fake.calls) == 2
    assert len(answers) == 1


def test_no_retry_on_400_and_gives_up_after_second_5xx(
    tmp_cache: Path, frames_dir: Path, checks_mock: list[CheckV1]
) -> None:
    rec = _recorded(checks_mock)
    bad = FakeClient(rec, fail_first=[400])
    with pytest.raises(_Err):
        v.verify(checks_mock[:1], frames_dir, client=bad)
    assert len(bad.calls) == 1
    flaky = FakeClient(rec, fail_first=[503, 503])
    with pytest.raises(_Err):
        v.verify(checks_mock[:1], frames_dir, client=flaky)
    assert len(flaky.calls) == 2


def test_missing_answer_is_left_out(tmp_cache: Path, frames_dir: Path, checks_mock: list[CheckV1]) -> None:
    class Empty(FakeClient):
        def _create(self, **kw: Any) -> Any:
            self.calls.append(kw)
            return SimpleNamespace(answers={})

    answers = v.verify(checks_mock[:2], frames_dir, client=Empty({}))
    assert answers == {}
    attached = v.attach(None, checks_mock[:2], answers, predict=_expected_predict)  # type: ignore[arg-type]
    assert all(c.result is None for c in attached)


def test_missing_frame_raises(
    tmp_cache: Path, tmp_path: Path, fake: FakeClient, checks_mock: list[CheckV1]
) -> None:
    with pytest.raises(FileNotFoundError):
        v.verify(checks_mock[:1], tmp_path / "nowhere", client=fake)


def test_wide_image_downscaled(tmp_path: Path) -> None:
    p = tmp_path / "wide.png"
    Image.new("RGB", (1600, 900), (1, 2, 3)).save(p)
    b64 = v._jpeg_b64(p.read_bytes())
    with Image.open(io.BytesIO(base64.b64decode(b64))) as im:
        assert im.format == "JPEG"
        assert im.size == (768, 432)


# --------------------------------------------------------------------------- attach / summary


def test_attach_is_scene_relative(
    tmp_cache: Path, frames_dir: Path, fake: FakeClient, checks_mock: list[CheckV1], scene_mock: SceneV1
) -> None:
    answers = v.verify(checks_mock, frames_dir, client=fake)
    side = next(c for c in checks_mock if c.template == "SIDE_LEFT" and not c.control)

    def flipped(_s: SceneV1, c: CheckV1) -> bool | str | None:
        return (not c.expected) if c.check_id == side.check_id else c.expected

    a = {c.check_id: c for c in v.attach(scene_mock, checks_mock, answers, predict=_expected_predict)}
    b = {c.check_id: c for c in v.attach(scene_mock, checks_mock, answers, predict=flipped)}
    assert a[side.check_id].result.verdict == "pass"
    assert b[side.check_id].result.verdict == "fail"
    assert answers[qkey(side)].verdict is None  # the bank itself never stores a verdict


def test_summary(tmp_cache: Path, checks_mock: list[CheckV1]) -> None:
    s = v.summary(checks_mock)
    assert (s["n"], s["pass"], s["fail"], s["uncertain"]) == (12, 9, 2, 1)
    assert s["pass_rate"] == pytest.approx(9 / 11)
    assert s["by_template"]["NOT_VISIBLE"] == {"n": 2, "pass": 0, "fail": 1, "uncertain": 1}
    assert s["yes_bias"] == pytest.approx(0.08)  # one control check in the mock


def test_summary_empty() -> None:
    s = v.summary([])
    assert s["pass_rate"] is None and s["yes_bias"] is None and s["n"] == 0


# --------------------------------------------------------------------------- ask


def test_ask_raw_and_cached(
    tmp_cache: Path, tmp_path: Path, fake: FakeClient, checks_mock: list[CheckV1]
) -> None:
    img = tmp_path / "f.jpg"
    _jpeg(img)
    q: Question = checks_mock[0].question
    r1 = v.ask(q, [img], "A single frame from one continuous film shot, at 00:01.000.", client=fake)
    r2 = v.ask(q, [img], "A single frame from one continuous film shot, at 00:01.000.", client=fake)
    assert r1 == r2
    assert r1.verdict is None and r1.p_yes == checks_mock[0].result.p_yes
    assert len(fake.calls) == 1


def test_requests_run_concurrently(tmp_cache: Path, tmp_path: Path, checks_mock: list[CheckV1]) -> None:
    """50 checks on 16 frames with 0.2 s simulated latency finish far faster than serially (3.2 s)."""
    import time

    base = next(c for c in checks_mock if c.input_ref.kind == "frame" and c.question.type == "noul")
    checks, rec = [], {}
    for i in range(50):
        f = f"frames/x/{(i % 16) * 1000:06d}.jpg"
        _jpeg(tmp_path / "fr" / f, color=(i, i, i))
        c = base.model_copy(
            update={
                "check_id": f"c{i:04d}",
                "t": float(i % 16),
                "input_ref": base.input_ref.model_copy(update={"paths": [f]}),
                "question": base.question.model_copy(update={"instructions": f"Question number {i}?"}),
            }
        )
        checks.append(c)
        rec[(v.check_context(c), c.question.instructions)] = base.result

    class Slow(FakeClient):
        def _create(self, **kw: Any) -> Any:
            time.sleep(0.2)
            return super()._create(**kw)

    fake = Slow(rec)
    t0 = time.perf_counter()
    answers = v.verify(checks, tmp_path / "fr", client=fake)
    assert len(answers) == 50
    assert len(fake.calls) == 16
    assert time.perf_counter() - t0 < 1.5
