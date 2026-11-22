"""CLI wiring for `run` and `eval` (no API calls)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from eval import run_all
from godshot import pipeline
from godshot.__main__ import app

runner = CliRunner()


def test_run_calls_pipeline(monkeypatch, tmp_path: Path) -> None:
    clip = tmp_path / "shot.mp4"
    clip.write_bytes(b"video")
    seen = {}

    def fake_run(c, o, *, max_rounds, render_reel):
        seen.update(clip=c, out=o, max_rounds=max_rounds, render_reel=render_reel)
        return SimpleNamespace(low_confidence=[1])

    monkeypatch.setattr(pipeline, "run", fake_run)
    out = tmp_path / "out"
    result = runner.invoke(app, ["run", str(clip), "-o", str(out), "--max-rounds", "1", "--no-reel"])
    assert result.exit_code == 0, result.output
    assert seen == {"clip": clip, "out": out, "max_rounds": 1, "render_reel": False}
    assert "1 low-confidence" in result.output


def test_run_missing_clip_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", str(tmp_path / "nope.mp4"), "-o", str(tmp_path / "out")])
    assert result.exit_code == 2


def test_eval_forwards_to_run_all(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(run_all, "_cli", lambda argv: seen.append(argv) or 0)
    result = runner.invoke(app, ["eval", "--split", "dev", "--system", "baseline", "--force"])
    assert result.exit_code == 0, result.output
    assert seen == [["--split", "dev", "--system", "baseline", "--force"]]


def test_eval_rejects_bad_system(monkeypatch) -> None:
    monkeypatch.setattr(run_all, "_cli", lambda argv: 0)
    assert runner.invoke(app, ["eval", "--system", "nope"]).exit_code == 2
