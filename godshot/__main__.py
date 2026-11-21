"""CLI entry point: python -m godshot <command>.

Spec: S00 (.claude/specs/S00*.md) — owner: Prajesh.
"""

from __future__ import annotations

import logging
import time

import typer

app = typer.Typer(help="God-Shot CLI", no_args_is_help=True)

_SMOKE_MARKERS = ("gemini-4", "decision", "omni")


def _model_path(model_id: str) -> str:
    return model_id if model_id.startswith("models/") else f"models/{model_id}"


def _smoke_list_models(client) -> bool:
    try:
        names = sorted(m.name for m in client.models.list() if m.name)
    except Exception as e:
        typer.echo(f"FAIL  list models: {type(e).__name__}: {e}")
        return False
    hits = [n for n in names if any(k in n.lower() for k in _SMOKE_MARKERS)]
    typer.echo(f"OK    list models ({len(names)} total, {len(hits)} relevant):")
    for n in hits:
        typer.echo(f"        {n}")
    return True


def _smoke_argon(client, model: str) -> bool:
    from google.genai import types

    t0 = time.perf_counter()
    try:
        resp = client.models.generate_content(
            model=model,
            contents="Reply with exactly the word OK.",
            config=types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level="LOW")),
        )
        text = (resp.text or "").strip()
    except Exception as e:
        typer.echo(f"FAIL  Argon ({model}): {type(e).__name__}: {e}")
        return False
    typer.echo(f"OK    Argon ({model}) in {time.perf_counter() - t0:.1f}s → {text[:40]!r}")
    return True


def _smoke_decisions(client, model: str) -> bool:
    t0 = time.perf_counter()
    try:
        resp = client.decisions.create(
            model=_model_path(model),
            input=[{"type": "text", "text": "The traffic light in the photo is glowing red."}],
            questions={
                "q1": {
                    "type": "noul",
                    "instructions": "Does the text say the traffic light is red?",
                    "criteria": {
                        "true_description": "The text states the light is red.",
                        "false_description": "The text states another color or no color.",
                    },
                }
            },
        )
        ans = (resp.answers or {}).get("q1")
        p_yes = getattr(ans, "noul", None)
    except Exception as e:
        typer.echo(f"FAIL  Decisions ({model}): {type(e).__name__}: {e}")
        return False
    if p_yes is None:
        typer.echo(f"FAIL  Decisions ({model}): no noul answer in response: {resp!r}"[:300])
        return False
    typer.echo(f"OK    Decisions ({model}) in {time.perf_counter() - t0:.1f}s → p_yes={p_yes:.2f}")
    return True


@app.command()
def smoke() -> None:
    """List models and make one tiny Argon + Decisions call (S00). Not cached: it's a liveness check."""
    from godshot.config import ConfigError, get_client, load_config

    try:
        client = get_client()
    except ConfigError as e:
        typer.echo(f"FAIL  config: {e}")
        raise typer.Exit(1) from e
    cfg = load_config()
    results = [
        _smoke_list_models(client),
        _smoke_argon(client, cfg.MODEL_EXTRACT),
        _smoke_decisions(client, cfg.MODEL_VERIFY),
    ]
    raise typer.Exit(0 if all(results) else 1)


@app.command()
def baseline(
    clip: str,
    o: str = typer.Option(..., "-o", help="Output dir, e.g. out/<clip_id>"),
    fps: int | None = typer.Option(None, "--fps", help="Video sampling fps (default GODSHOT_FPS)"),
) -> None:
    """Single Argon call → scene_r0.json (S02)."""
    from pathlib import Path

    from godshot.extract import baseline as run_baseline

    scene = run_baseline(Path(clip), Path(o), fps=fps)
    n_keys = len(scene.camera.track)
    counts = f"{len(scene.objects)} objects, {len(scene.actors)} actors, {n_keys} camera keys"
    typer.echo(f"wrote {Path(o) / 'scene_r0.json'} ({counts})")


@app.command()
def run(clip: str, o: str = typer.Option(..., "-o")) -> None:
    """Full loop + reel (S06, S07, S08)."""
    raise NotImplementedError("S06/S07/S08")


@app.command("eval")
def eval_(split: str = "test", system: str = "full") -> None:
    """Run eval over a split (S11)."""
    raise NotImplementedError("S11")


@app.command()
def web(
    host: str = "127.0.0.1",
    port: int = 8000,
    mock: bool = typer.Option(False, "--mock", help="Seed out/mock_001 from mocks/ (marked MOCK)."),
) -> None:
    """Start the local UI on :8000 (S09). GODSHOT_OFFLINE=1 → read-only cached demo."""
    import uvicorn

    from godshot.web.app import app as web_app
    from godshot.web.app import seed_mock_run

    if mock:
        typer.echo(f"seeded {seed_mock_run()}")
    typer.echo(f"God-Shot UI: http://{host}:{port}")
    uvicorn.run(web_app, host=host, port=port, log_level="info")


@app.command()
def reshoot(
    clip: str,
    orig: str = typer.Option(..., "--orig", help="Original scene JSON (e.g. out/<id>/scene_final.json)."),
    edited: str = typer.Option(..., "--edited", help="Edited scene JSON (from the map editor)."),
    o: str = typer.Option(..., "-o", help="Output folder."),
    n: int = typer.Option(1, "--n", help="Takes to generate; the best by conformance is kept."),
    mode: str = typer.Option("blockout", "--mode", help="blockout (map-exact guide) | text (fallback)."),
    resolution: str = typer.Option("360p", "--resolution"),
    duration: str | None = typer.Option(
        None, "--duration", help="Cap, e.g. 6s (default: the original clip's length)."
    ),
    direct: bool = typer.Option(False, "--direct", help="S14: scene analyst + director agents."),
    preserve: bool = typer.Option(False, "--preserve", help="S14: score preservation without directing."),
    w_preserve: float = typer.Option(0.4, "--w-preserve", help="S14: weight of the scene scores in ranking."),
    request: str = typer.Option("", "--request", help='S14: free-text change, e.g. "make him limp"'),
    critic: int = typer.Option(0, "--critic", help="S14: critic rounds (each may add one corrected take)."),
) -> None:
    """Edited map → AI-generated re-shoot + conformance score (S12); --direct adds S14 agents."""
    from pathlib import Path

    from godshot.reshoot import reshoot_best
    from godshot.schema import SceneV1

    r = reshoot_best(
        Path(clip), SceneV1.load(orig), SceneV1.load(edited), Path(o), n=n,
        mode=mode, resolution=resolution, duration=duration,
        direct=direct, score_preservation=preserve, w_preserve=w_preserve,
        request=request, critic_rounds=critic,
    )  # fmt: skip
    typer.echo(f"{r['label']}: {r['video']}")
    score = f"{r['score']:.2f} ({r['hits']}/{r['usable']})"
    typer.echo(f"conformance vs edited map: {score}, best take {r['best_take']}/{n}")
    if "preservation" in r:
        typer.echo(f"preservation of the scene: {r['preservation']:.2f} ({r['kept']} locked items kept)")
        if r.get("fulfilment") is not None:
            typer.echo(f"request fulfilled: {r['fulfilment']:.2f} ({r['done']} changes seen)")
        for c in r.get("critiques", []):
            typer.echo(
                f"critic round {c['round']} on take {c['on_take']}: {c['verdict']} "
                f"({len(c['issues'])} issues)"
            )
        typer.echo(f"combined score: {r['combined']:.2f}")


@app.command()
def conformance(
    video: str,
    edited: str = typer.Option(..., "--edited", help="The scene JSON the video should follow."),
) -> None:
    """Score any video against a scene with S04 checks + S05 verifier (S12)."""
    from pathlib import Path

    from godshot.reshoot import conformance as score
    from godshot.schema import SceneV1

    r = score(Path(video), SceneV1.load(edited))
    by = ", ".join(
        f"{k} {v['hits']}/{v['usable']}" for k, v in sorted(r["by_template"].items()) if v["usable"]
    )
    typer.echo(f"conformance: {r['score']:.2f} ({r['hits']}/{r['usable']} usable of {r['n_checks']}) | {by}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app()


if __name__ == "__main__":
    main()
