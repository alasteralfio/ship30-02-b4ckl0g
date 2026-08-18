"""Config behaviour: the key is returned when present, and its absence fails
loudly with a message that says how to fix it."""

import pytest

from b4cklog import config
from b4cklog.config import MissingConfigError, steam_api_key


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """Run each test against a clean, controlled environment.

    `load_env` is a no-op here so a real local .env can't leak the key into
    the missing-key test and mask the failure we're checking for.
    """
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.delenv("STEAM_API_KEY", raising=False)
    yield


def test_returns_key_when_present(monkeypatch):
    monkeypatch.setenv("STEAM_API_KEY", "ABC123")
    assert steam_api_key() == "ABC123"


def test_strips_surrounding_whitespace(monkeypatch):
    monkeypatch.setenv("STEAM_API_KEY", "  ABC123  ")
    assert steam_api_key() == "ABC123"


def test_raises_when_absent():
    with pytest.raises(MissingConfigError) as exc:
        steam_api_key()
    message = str(exc.value)
    assert "STEAM_API_KEY" in message
    assert "steamcommunity.com/dev/apikey" in message


def test_raises_when_blank(monkeypatch):
    monkeypatch.setenv("STEAM_API_KEY", "   ")
    with pytest.raises(MissingConfigError):
        steam_api_key()
