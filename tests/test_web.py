"""S09 web tests: FastAPI routes on a temp out/ dir seeded from mocks (no network, no ffmpeg)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from godshot.config import load_config
from godshot.web import app as web


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(web, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(web, "EXTRA_RUN_DIRS", [])
    monkeypatch.setattr(web, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(web, "CLIP_DIRS", [tmp_path / "uploads"])
    monkeypatch.setenv("GODSHOT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GODSHOT_OFFLINE", raising=False)
    load_config.cache_clear()
    web.seed_mock_run(tmp_path / "out")
    yield TestClient(web.app)
    load_config.cache_clear()


def test_index_and_static(client: TestClient) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/static/index.html"
    assert "God-Shot" in client.get("/static/index.html").text
    assert "camAt" in client.get("/static/app.js").text


def test_runs_and_run_payload(client: TestClient) -> None:
    runs = client.get("/api/runs").json()
    assert [r["clip_id"] for r in runs] == ["mock_001"]
    assert runs[0]["mock"] is True and runs[0]["pass_rates"] == [pytest.approx(9 / 11)]
    run = client.get("/api/run/mock_001").json()
    assert run["scene"]["clip"]["id"] == "mock_001"
    assert list(run["checks"]) == ["0"] and len(run["checks"]["0"]) == 12
    assert run["reel_url"] is None and run["scene_edit"] is None
    assert client.get("/api/run/nope").status_code == 404
    assert client.get("/api/run/..%2F..").status_code in (400, 404)


def test_apply_preset_preview_does_not_save(client: TestClient, tmp_path: Path) -> None:
    r = client.post(
        "/api/run/mock_001/apply", json={"edit": {"op": "preset", "move": "dolly_out", "t0": 0, "t1": 4}}
    )
    assert r.status_code == 200
    assert r.json()["scene"]["camera"]["motion_segments"][0]["translation"] == "dolly_out"
    assert not list((tmp_path / "out" / "mock_001").glob("scene_edit_*.json"))
    bad = client.post(
        "/api/run/mock_001/apply", json={"edit": {"op": "preset", "move": "zoom", "t0": 0, "t1": 4}}
    )
    assert bad.status_code == 400


@pytest.fixture
def fake_render(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Stand-ins for S07 render_map / S08 compose_reel (no ffmpeg needed); records calls."""
    from godshot import compose, render

    calls: list[dict] = []

    def render_map(scene, out, fps=None, size=(1080, 960), ghost=None):
        calls.append({"fn": "render_map", "out": Path(out).name, "ghost": ghost is not None})
        Path(out).write_bytes(b"map")
        return Path(out)

    def compose_reel(clip, map_mp4, out, title=None, credit=None):
        calls.append({"fn": "compose_reel", "clip": Path(clip).name, "map": Path(map_mp4).name})
        Path(out).write_bytes(b"reel")
        return Path(out)

    monkeypatch.setattr(render, "render_map", render_map)
    monkeypatch.setattr(compose, "compose_reel", compose_reel)
    return calls


def test_edit_saves_even_when_render_is_unavailable(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from godshot import render

    def no_ffmpeg(*a, **k):
        raise render.RenderError("ffmpeg not found on PATH.")

    monkeypatch.setattr(render, "render_map", no_ffmpeg)
    scene = client.get("/api/run/mock_001").json()["scene"]
    scene["camera"]["hfov_deg"] = 50
    r = client.post("/api/run/mock_001/edit", json={"scene": scene})
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == "scene_edit_1.json" and body["render"] == "error" and "ffmpeg" in body["note"]
    assert (tmp_path / "out" / "mock_001" / "scene_edit_1.json").exists()  # the edit is never lost
    r2 = client.post("/api/run/mock_001/edit", json={"edit": {"op": "set_fov", "hfov_deg": 70}})
    assert r2.json()["saved"] == "scene_edit_2.json"
    assert client.get("/api/run/mock_001").json()["scene_edit"]["camera"]["hfov_deg"] == 70


def test_edit_renders_map_and_reel_with_ghost(
    client: TestClient, tmp_path: Path, fake_render: list[dict]
) -> None:
    d = tmp_path / "out" / "mock_001"
    (d / "clip.mp4").write_bytes(b"clip")
    r = client.post(
        "/api/run/mock_001/edit",
        json={"edit": {"op": "preset", "move": "arc_right", "t0": 0, "t1": 10, "target": "a1"}},
    )
    body = r.json()
    assert body["render"] == "ok" and body["reel_url"].startswith("/out/mock_001/reel_edit_1.mp4")
    assert fake_render == [
        {"fn": "render_map", "out": "map_edit_1.mp4", "ghost": True},
        {"fn": "compose_reel", "clip": "clip.mp4", "map": "map_edit_1.mp4"},
    ]
    run = client.get("/api/run/mock_001").json()
    assert run["reel_edit_url"].startswith("/out/mock_001/reel_edit_1.mp4")
    assert run["clip_url"].startswith("/out/mock_001/clip.mp4")


def test_edit_without_clip_renders_map_only(client: TestClient, fake_render: list[dict]) -> None:
    body = client.post("/api/run/mock_001/edit", json={"edit": {"op": "set_fov", "hfov_deg": 40}}).json()
    assert body["render"] == "map_only" and body["map_url"].startswith("/out/mock_001/map_edit_1.mp4")
    assert [c["fn"] for c in fake_render] == ["render_map"]


def test_text_edit_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from godshot import edit as ed

    def fake_text_edit(scene, instruction):
        return ed.apply_preset(scene, 0, 10, "arc_right", 20, "a1")

    monkeypatch.setattr(web, "text_edit", fake_text_edit)
    r = client.post("/api/run/mock_001/text-edit", json={"instruction": "slow arc right around the actor"})
    assert r.status_code == 200
    assert r.json()["scene"]["camera"]["motion_segments"][0]["translation"] == "arc_right"
    assert client.post("/api/run/mock_001/text-edit", json={"instruction": "  "}).status_code == 400


def test_offline_mode_is_read_only(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GODSHOT_OFFLINE", "1")
    assert client.get("/api/status").json()["offline"] is True
    assert client.get("/api/run/mock_001").status_code == 200
    assert (
        client.post("/api/run/mock_001/edit", json={"edit": {"op": "set_fov", "hfov_deg": 70}}).status_code
        == 403
    )
    assert client.post("/api/run?name=x.mp4", content=b"x").status_code == 403
    # previewing a preset is still allowed offline (pure function, nothing saved)
    assert (
        client.post("/api/run/mock_001/apply", json={"edit": {"op": "set_fov", "hfov_deg": 70}}).status_code
        == 200
    )


def test_frame_route_is_confined(client: TestClient) -> None:
    assert client.get("/frame", params={"path": "../../../etc/passwd"}).status_code == 403
    assert client.get("/frame", params={"path": "godshot/schema.py"}).status_code == 403
    assert client.get("/frame", params={"path": "frames/mock_001/001000.jpg"}).status_code in (403, 404)


def _wait(client: TestClient, job_id: str) -> dict:
    for _ in range(100):
        j = client.get(f"/api/job/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_upload_starts_job_and_reports_pipeline_errors(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from godshot import pipeline

    def fake_run(clip: Path, out_dir: Path) -> None:
        (out_dir / "loop_log.json").write_text('[{"round": 0, "pass_rate": 0.5}]', encoding="utf-8")
        raise NotImplementedError("S06")

    monkeypatch.setattr(pipeline, "run", fake_run)
    r = client.post("/api/run?name=my shot!.mp4", content=b"\x00fakevideo")
    assert r.status_code == 200
    clip_id = r.json()["clip_id"]
    assert clip_id.startswith("my_shot_")
    assert (tmp_path / "uploads" / f"{clip_id}.mp4").read_bytes() == b"\x00fakevideo"
    j = _wait(client, r.json()["job_id"])
    assert j["status"] == "error" and "not implemented" in j["error"]
    assert j["loop_log"] == [{"round": 0, "pass_rate": 0.5}]
    assert client.post("/api/run", content=b"").status_code == 400
    assert client.get("/api/job/nope").status_code == 404


def test_reshoot_without_clip_is_404(client: TestClient) -> None:
    assert client.post("/api/run/mock_001/reshoot", json={}).status_code == 404


def test_loop_log_s06_bank_fields_map_to_pass_rate(client: TestClient, tmp_path: Path) -> None:
    """S06 writes score_cand/bank_size; the UI needs contract pass_rate/n_checks."""
    import json

    log = [
        {"round": 0, "bank_size": 60, "usable": 54, "score_prev": 0.85, "score_cand": 0.85, "accepted": True},
        {"round": 1, "bank_size": 73, "usable": 67, "score_prev": 0.76, "score_cand": 1.0, "accepted": True},
        {"round": 2, "pass_rate": 0.5, "n_checks": 10, "accepted": False},  # contract shape is kept as-is
    ]
    (tmp_path / "out" / "mock_001" / "loop_log.json").write_text(json.dumps(log), encoding="utf-8")
    runs = client.get("/api/runs").json()
    assert runs[0]["pass_rates"] == [0.85, 1.0, 0.5]
    got = client.get("/api/run/mock_001").json()["loop_log"]
    assert [e["n_checks"] for e in got] == [60, 73, 10]
    assert got[1]["score_prev"] == 0.76  # original fields are preserved


def test_reshoot_job_numbers_takes_scores_and_builds_compare(
    client: TestClient, tmp_path: Path, fake_render: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    from godshot import compose, reshoot

    d = tmp_path / "out" / "mock_001"
    (d / "clip.mp4").write_bytes(b"clip")
    seen: list[tuple] = []

    def fake_reshoot(clip, scene_orig, scene_edited, out, **kw):
        seen.append((Path(clip).name, Path(out).name, scene_edited.camera.hfov_deg))
        Path(out).write_bytes(b"omni")
        return Path(out)

    def fake_conformance(video, scene_edited, work_dir=None):
        return {"score": 0.9, "hits": 27, "usable": 30, "checks": [{"big": "list"}]}

    def fake_compare(a, b, m, out):
        seen.append(("compare", Path(a).name, Path(b).name, Path(m).name))
        Path(out).write_bytes(b"cmp")
        return Path(out)

    monkeypatch.setattr(reshoot, "reshoot", fake_reshoot)
    monkeypatch.setattr(reshoot, "conformance", fake_conformance)
    monkeypatch.setattr(compose, "compose_compare", fake_compare)

    scene = client.get("/api/run/mock_001").json()["scene"]
    scene["camera"]["hfov_deg"] = 42
    for take in (1, 2):
        j = _wait(client, client.post("/api/run/mock_001/reshoot", json={"scene": scene}).json()["job_id"])
        assert j["status"] == "done", j
        assert j["conformance"] == {"score": 0.9, "hits": 27, "usable": 30}  # no bulky check list in the job
        assert j["reshoot_url"].startswith(f"/out/mock_001/reshoot_{take}.mp4")
        assert j["compare_url"].startswith(f"/out/mock_001/compare_{take}.mp4")
        assert (d / f"scene_reshoot_{take}.json").exists()
    assert seen[0] == ("clip.mp4", "reshoot_1.mp4", 42)
    assert seen[1] == ("compare", "clip.mp4", "reshoot_1.mp4", "map_reshoot_1.mp4")
    assert {"fn": "render_map", "out": "map_reshoot_1.mp4", "ghost": True} in fake_render
    run = client.get("/api/run/mock_001").json()
    assert run["reshoot_url"].startswith("/out/mock_001/reshoot_2.mp4")
    assert run["compare_url"].startswith("/out/mock_001/compare_2.mp4")


def test_reshoot_keeps_video_when_compare_fails(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from godshot import render, reshoot

    (tmp_path / "out" / "mock_001" / "clip.mp4").write_bytes(b"clip")
    monkeypatch.setattr(
        reshoot, "reshoot", lambda c, o, e, out, **k: (Path(out).write_bytes(b"x"), Path(out))[1]
    )
    monkeypatch.setattr(
        reshoot, "conformance", lambda v, e, work_dir=None: {"score": 0.5, "hits": 1, "usable": 2}
    )

    def boom(*a, **k):
        raise render.RenderError("ffmpeg not found on PATH.")

    monkeypatch.setattr(render, "render_map", boom)
    j = _wait(client, client.post("/api/run/mock_001/reshoot", json={}).json()["job_id"])
    assert j["status"] == "done" and j["compare_url"] is None and j["reshoot_url"]
