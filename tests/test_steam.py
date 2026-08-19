"""Steam client: visibility-first reads, the two can't-read states, achievement
parsing and the no-stats case, and bounded, deterministic sampling.

All against recorded fixtures via httpx MockTransport — no live key needed. Async
coroutines are driven with asyncio.run so the suite needs no async-test plugin.
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from b4cklog.steam import (
    AchievementStats,
    Library,
    PrivateProfile,
    SteamClient,
    UnknownSteamID,
    UnreadableReason,
)
from b4cklog.steam.models import OwnedGame

FIXTURES = Path(__file__).parent / "fixtures" / "steam"

# Real accounts the fixtures were recorded from.
PUBLIC_ID = "76561197960434622"          # public, 1085-game library
PROFILE_PRIVATE_ID = "76561197960287930"  # community profile not public (vis 2)
GAMES_PRIVATE_ID = "76561197972495328"    # profile public, game details hidden
UNKNOWN_ID = "1"                          # matches no account


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _client(handler) -> SteamClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://api.steampowered.com")
    return SteamClient("test-key", client=http)


def _fixture_handler(requests: list | None = None):
    """Route recorded fixtures by the same params the real endpoints key on."""

    summaries = {
        PUBLIC_ID: "summary_public.json",
        PROFILE_PRIVATE_ID: "summary_profile_private.json",
        GAMES_PRIVATE_ID: "summary_games_private.json",
        UNKNOWN_ID: "summary_unknown.json",
    }
    owned = {
        PUBLIC_ID: "owned_games_public.json",
        GAMES_PRIVATE_ID: "owned_games_private.json",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        path = request.url.path
        params = request.url.params
        if path.endswith("GetPlayerSummaries/v2/"):
            return httpx.Response(200, json=_load(summaries[params["steamids"]]))
        if path.endswith("GetOwnedGames/v1/"):
            return httpx.Response(200, json=_load(owned[params["steamid"]]))
        if path.endswith("GetPlayerAchievements/v1/"):
            body = _load(f"achievements_{params['appid']}.json")
            # Steam answers a no-stats game with 400 + success:false; mirror that
            # so the client's status handling is exercised, not bypassed.
            status = 200 if body["playerstats"].get("success") else 400
            return httpx.Response(status, json=body)
        raise AssertionError(f"unexpected path {path}")

    return handler


# --- read_library: the happy path and the two can't-read states ---

def test_public_profile_returns_library():
    client = _client(_fixture_handler())
    result = asyncio.run(client.read_library(PUBLIC_ID))
    assert isinstance(result, Library)
    assert result.persona_name == "al"
    assert len(result.games) == 1085
    top = result.most_played(1)[0]
    assert top.name == "Factorio"
    assert top.playtime_minutes == 48726


def test_profile_private_returns_cant_read():
    client = _client(_fixture_handler())
    result = asyncio.run(client.read_library(PROFILE_PRIVATE_ID))
    assert isinstance(result, PrivateProfile)
    assert result.reason is UnreadableReason.PROFILE_PRIVATE
    # persona name still comes through — the summary is readable even when the
    # library isn't, and the report can name the player.
    assert result.persona_name == "Rabscuttle"


def test_public_profile_with_hidden_games_returns_cant_read():
    client = _client(_fixture_handler())
    result = asyncio.run(client.read_library(GAMES_PRIVATE_ID))
    assert isinstance(result, PrivateProfile)
    assert result.reason is UnreadableReason.GAMES_PRIVATE


def test_unknown_steam_id_raises():
    client = _client(_fixture_handler())
    with pytest.raises(UnknownSteamID):
        asyncio.run(client.read_library(UNKNOWN_ID))


# --- achievements: parsing and the no-stats case ---

def test_achievements_parse_unlocked_and_total():
    client = _client(_fixture_handler())
    stats = asyncio.run(client.get_player_achievements(PUBLIC_ID, 427520))
    assert stats == AchievementStats(app_id=427520, unlocked=77, total=88)
    assert stats.completion_rate == pytest.approx(77 / 88)


def test_game_with_no_stats_returns_none():
    client = _client(_fixture_handler())
    stats = asyncio.run(client.get_player_achievements(PUBLIC_ID, 1594320))
    assert stats is None


# --- sampling: exact call count, no-stats dropped, deterministic order ---

def _synthetic_library(playtimes: dict[int, int]) -> Library:
    games = tuple(OwnedGame(app_id, f"game-{app_id}", mins) for app_id, mins in playtimes.items())
    return Library(steam_id="synthetic", persona_name=None, games=games)


def test_sample_issues_exactly_one_call_per_most_played_game():
    requests: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        appid = int(request.url.params["appid"])
        return httpx.Response(
            200,
            json={"playerstats": {"success": True, "achievements": [{"achieved": 1}]}},
        )

    client = _client(handler)
    # Five games with playtime, one unplayed; sampling the top 3 must hit exactly
    # those three and never the unplayed one.
    library = _synthetic_library({10: 500, 20: 400, 30: 300, 40: 200, 50: 0})
    result = asyncio.run(client.sample_achievements(library, top_n=3))

    assert len(requests) == 3
    sampled = sorted(int(r.url.params["appid"]) for r in requests)
    assert sampled == [10, 20, 30]
    assert set(result) == {10, 20, 30}


def test_all_unplayed_library_issues_no_calls():
    requests: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"playerstats": {"success": False}})

    client = _client(handler)
    library = _synthetic_library({10: 0, 20: 0})
    result = asyncio.run(client.sample_achievements(library))
    assert requests == []
    assert result == {}


def test_sample_drops_games_without_achievements():
    # Real fixtures: 427520/1245620 have achievements, 1594320 has no stats.
    client = _client(_fixture_handler())
    library = _synthetic_library({427520: 48726, 1245620: 32765, 1594320: 8592})
    result = asyncio.run(client.sample_achievements(library, top_n=3))
    assert set(result) == {427520, 1245620}


def test_most_played_ordering_is_deterministic():
    # Equal playtimes must break ties by app_id, so the ranking never wobbles.
    library = _synthetic_library({30: 100, 10: 100, 20: 100})
    order = [g.app_id for g in library.most_played()]
    assert order == [10, 20, 30]
