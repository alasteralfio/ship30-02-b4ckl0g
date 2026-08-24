"""Keyless game-data client: appdetails parsing, the delisted-app and
no-achievements cases, and retry-then-give-up on transient failures.

All against httpx MockTransport — no live network needed.
"""

import asyncio

import httpx
import pytest

from b4cklog.steam.gamedata import (
    AchievementPercent,
    AppDetails,
    GameDataClient,
    GameDataError,
)


def _client(handler) -> GameDataClient:
    return GameDataClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        min_request_interval=0.0,
    )


def _appdetails_response(app_id: int, **overrides) -> dict:
    data = {
        "name": "Portal 2",
        "type": "game",
        "genres": [{"id": "1", "description": "Action"}],
        "categories": [{"id": 22, "description": "Steam Achievements"}],
        "achievements": {"total": 51},
        **overrides,
    }
    return {str(app_id): {"success": True, "data": data}}


# --- appdetails: the happy path and the delisted / non-game cases ---


def test_app_details_parses_genres_categories_and_achievement_count():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_appdetails_response(620))

    client = _client(handler)
    details = asyncio.run(client.get_app_details(620))
    assert details == AppDetails(
        app_id=620,
        name="Portal 2",
        type="game",
        genres=("Action",),
        categories=("Steam Achievements",),
        achievement_count=51,
    )


def test_app_details_with_no_achievements_field_returns_zero_count():
    def handler(request: httpx.Request) -> httpx.Response:
        data = _appdetails_response(620)
        del data["620"]["data"]["achievements"]
        return httpx.Response(200, json=data)

    client = _client(handler)
    details = asyncio.run(client.get_app_details(620))
    assert details.achievement_count == 0


def test_delisted_app_returns_none_not_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"9999999": {"success": False}})

    client = _client(handler)
    assert asyncio.run(client.get_app_details(9999999)) is None


# --- global achievement percentages: the happy path and no-stats case ---


def test_global_percentages_parsed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "achievementpercentages": {
                    "achievements": [
                        {"name": "A", "percent": "80.9"},
                        {"name": "B", "percent": "12.5"},
                    ]
                }
            },
        )

    client = _client(handler)
    result = asyncio.run(client.get_global_achievement_percentages(620))
    assert result == [
        AchievementPercent("A", 80.9),
        AchievementPercent("B", 12.5),
    ]


def test_invalid_gameid_returns_none_not_an_error():
    # Steam answers an invalid/no-stats gameid with 403 and an empty body.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={})

    client = _client(handler)
    assert asyncio.run(client.get_global_achievement_percentages(1)) is None


# --- retry: transient failures back off then give up, no key to leak here ---


def _no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)


def test_transient_error_is_retried_then_succeeds(monkeypatch):
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(200, json=_appdetails_response(620))

    client = _client(handler)
    details = asyncio.run(client.get_app_details(620))
    assert details.name == "Portal 2"
    assert calls["n"] == 3


def test_persistent_transient_error_raises_game_data_error(monkeypatch):
    _no_sleep(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    client = _client(handler)
    with pytest.raises(GameDataError):
        asyncio.run(client.get_app_details(620))


def test_transport_failure_is_retried_then_raises(monkeypatch):
    _no_sleep(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("reset", request=request)

    client = _client(handler)
    with pytest.raises(GameDataError):
        asyncio.run(client.get_app_details(620))
