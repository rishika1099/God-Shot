"""Shared fixtures: mock scene/checks from mocks/ (S00)."""

from __future__ import annotations

from pathlib import Path

import pytest

from godshot.schema import CheckV1, SceneV1, load_checks

MOCKS = Path(__file__).resolve().parent.parent / "mocks"


@pytest.fixture
def scene_mock_path() -> Path:
    return MOCKS / "scene_mock.json"


@pytest.fixture
def checks_mock_path() -> Path:
    return MOCKS / "checks_mock.json"


@pytest.fixture
def scene_mock(scene_mock_path: Path) -> SceneV1:
    return SceneV1.load(scene_mock_path)


@pytest.fixture
def checks_mock(checks_mock_path: Path) -> list[CheckV1]:
    return load_checks(checks_mock_path)
