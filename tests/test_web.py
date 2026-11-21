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


def test_edit_saves_and_reports_pending_render(client: TestClient, tmp_path: Path) -> None:
    scene = client.get("/api/run/mock_001").json()["scene"]
    scene["camera"]["hfov_deg"] = 50
    r = client.post("/api/run/mock_001/edit", json={"scene": scene})
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == "scene_edit_1.json" and body["render"] == "pending"
    assert (tmp_path / "out" / "mock_001" / "scene_edit_1.json").exists()
    r2 = client.post("/api/run/mock_001/edit", json={"edit": {"op": "set_fov", "hfov_deg": 70}})
    assert r2.json()["saved"] == "scene_edit_2.json"
    assert client.get("/api/run/mock_001").json()["scene_edit"]["camera"]["hfov_deg"] == 70


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
