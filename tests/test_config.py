"""S00: config loading and the shared Gemini client (no network)."""

from __future__ import annotations

import pytest

from godshot.config import ConfigError, get_client, load_config


@pytest.fixture
def fresh(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("godshot.config.load_dotenv", lambda *a, **k: False)  # ignore the real .env
    load_config.cache_clear()
    get_client.cache_clear()
    yield monkeypatch
    load_config.cache_clear()
    get_client.cache_clear()


def test_client_is_shared_and_survives_inline_use(fresh: pytest.MonkeyPatch) -> None:
    fresh.setenv("GEMINI_API_KEY", "test-key-not-real")
    a = get_client()
    assert get_client() is a  # one client per process
    pager_owner = get_client().models  # inline use must not close the shared client
    del pager_owner
    assert get_client() is a


def test_missing_key_raises_and_is_not_cached(fresh: pytest.MonkeyPatch) -> None:
    fresh.delenv("GEMINI_API_KEY", raising=False)
    fresh.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        get_client()
    fresh.setenv("GEMINI_API_KEY", "test-key-not-real")
    load_config.cache_clear()
    assert get_client() is not None  # the earlier failure wasn't cached


def test_config_repr_hides_key(fresh: pytest.MonkeyPatch) -> None:
    fresh.setenv("GEMINI_API_KEY", "super-secret-value")
    cfg = load_config()
    assert "super-secret-value" not in repr(cfg)
    assert cfg.MODEL_VERIFY and cfg.FPS > 0
