"""CLI entry point: python -m godshot <command>.

Spec: S00 (.claude/specs/S00*.md) — owner: Prajesh.
Implement per the spec; keep signatures in sync with it.
"""

from __future__ import annotations

import typer

app = typer.Typer(help="God-Shot CLI")


@app.command()
def smoke() -> None:
    """List models and make one tiny Argon + Decisions call (S00)."""
    raise NotImplementedError


@app.command()
def baseline(clip: str, o: str = typer.Option(..., "-o")) -> None:
    """Single Argon call → scene_r0.json (S02)."""
    raise NotImplementedError


@app.command()
def run(clip: str, o: str = typer.Option(..., "-o")) -> None:
    """Full loop + reel (S06, S07, S08)."""
    raise NotImplementedError


@app.command("eval")
def eval_(split: str = "test", system: str = "full") -> None:
    """Run eval over a split (S11)."""
    raise NotImplementedError


@app.command()
def web() -> None:
    """Start the local UI on :8000 (S09)."""
    raise NotImplementedError


if __name__ == "__main__":
    app()
