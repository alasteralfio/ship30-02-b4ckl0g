"""The seed crawl (Goal 3.2): a public profile persists with only its played
games; a private one is logged and skipped; and a re-run is idempotent and
refetches nothing. MockTransport + in-memory store, no live key."""

import asyncio
import json

import httpx

from b4cklog import store
from b4cklog.steam import SteamClient
from pipeline import crawl_seed
from pipeline.crawl_seed import crawl_seed as run_crawl

PUBLIC = "public1"
PRIVATE = "private1"

# Two played games and one owned-but-unplayed; the unplayed one must not be
# stored, since seed playtimes track invested time.
_GAMES = [
    {"appid": 10, "name": "A", "playtime_forever": 600},
    {"appid": 20, "name": "B", "playtime_forever": 120},
    {"appid": 30, "name": "C", "playtime_forever": 0},
]


def _handler(requests: list):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path, params = request.url.path, request.url.params
        if path.endswith("GetPlayerSummaries/v2/"):
            players = [
                {
                    "steamid": sid,
                    "personaname": sid,
                    "communityvisibilitystate": 2 if sid == PRIVATE else 3,
                }
                for sid in params["steamids"].split(",")
            ]
            return httpx.Response(200, json={"response": {"players": players}})
        if path.endswith("GetOwnedGames/v1/"):
            return httpx.Response(200, json={"response": {"game_count": 3, "games": _GAMES}})
        if path.endswith("GetPlayerAchievements/v1/"):
            return httpx.Response(
                200,
                json={"playerstats": {"success": True, "achievements": [{"achieved": 1}, {"achieved": 0}]}},
            )
        raise AssertionError(f"unexpected path {path}")

    return handler


def _client(requests: list) -> SteamClient:
    transport = httpx.MockTransport(_handler(requests))
    http = httpx.AsyncClient(transport=transport, base_url="https://api.steampowered.com")
    return SteamClient("test-key", client=http)


def _conn():
    conn = store.connect(":memory:")
    store.init_db(conn)
    return conn


def test_public_profile_persists_with_only_played_games(tmp_path):
    conn = _conn()
    skip_log = tmp_path / "skips.jsonl"
    counts = asyncio.run(
        run_crawl([PUBLIC], conn, _client([]), skip_log_path=skip_log)
    )

    assert counts["crawled"] == 1
    assert store.get_seed_profile(conn, PUBLIC) is not None
    # The 0-minute game is dropped; only invested time is stored.
    assert store.get_seed_playtimes(conn, PUBLIC) == {10: 600, 20: 120}


def test_private_profile_is_logged_and_skipped(tmp_path):
    conn = _conn()
    skip_log = tmp_path / "skips.jsonl"
    counts = asyncio.run(
        run_crawl([PRIVATE], conn, _client([]), skip_log_path=skip_log)
    )

    assert counts["private"] == 1
    assert store.get_seed_profile(conn, PRIVATE) is None
    logged = [json.loads(line) for line in skip_log.read_text().splitlines()]
    assert logged == [{"steam_id": PRIVATE, "reason": "profile_private", "at": logged[0]["at"]}]


def _client_with(handler) -> SteamClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://api.steampowered.com")
    return SteamClient("secret", client=http)


def test_achievement_server_error_does_not_crash_the_profile(tmp_path):
    # Achievements 500 for every game: the profile must still persist (with no
    # completion signal), not abort the crawl.
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("GetPlayerSummaries/v2/"):
            return httpx.Response(200, json={"response": {"players": [
                {"steamid": PUBLIC, "personaname": PUBLIC, "communityvisibilitystate": 3}]}})
        if path.endswith("GetOwnedGames/v1/"):
            return httpx.Response(200, json={"response": {"game_count": 3, "games": _GAMES}})
        return httpx.Response(500, text="Internal Server Error")  # achievements

    conn = _conn()
    counts = asyncio.run(
        run_crawl([PUBLIC], conn, _client_with(handler), skip_log_path=tmp_path / "s.jsonl")
    )
    assert counts["crawled"] == 1
    assert store.get_seed_profile(conn, PUBLIC)["completion_drive"] == 0.0


def test_http_error_skips_one_profile_and_keeps_going(tmp_path):
    # GetOwnedGames 500s for the first ID and succeeds for the second. The first
    # is logged (key-free) and retried; the second is crawled. No crash.
    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        if path.endswith("GetPlayerSummaries/v2/"):
            players = [
                {"steamid": sid, "personaname": sid, "communityvisibilitystate": 3}
                for sid in params["steamids"].split(",")
            ]
            return httpx.Response(200, json={"response": {"players": players}})
        if path.endswith("GetOwnedGames/v1/"):
            if params["steamid"] == "bad":
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json={"response": {"game_count": 3, "games": _GAMES}})
        return httpx.Response(200, json={"playerstats": {"success": True, "achievements": [{"achieved": 1}]}})

    conn = _conn()
    skip_log = tmp_path / "s.jsonl"
    counts = asyncio.run(run_crawl(["bad", PUBLIC], conn, _client_with(handler), skip_log_path=skip_log))

    assert counts["errors"] == 1
    assert counts["crawled"] == 1
    assert store.get_seed_profile(conn, "bad") is None
    assert store.get_seed_profile(conn, PUBLIC) is not None
    logged = [json.loads(line) for line in skip_log.read_text().splitlines()]
    assert logged[0]["steam_id"] == "bad"
    assert logged[0]["reason"] == "error: HTTP 500"  # status only — the key never lands on disk
    assert "secret" not in skip_log.read_text()


def test_rerun_is_idempotent_and_refetches_nothing(tmp_path):
    conn = _conn()
    skip_log = tmp_path / "skips.jsonl"
    ids = [PUBLIC, PRIVATE]
    asyncio.run(run_crawl(ids, conn, _client([]), skip_log_path=skip_log))

    # Second run: the public profile is already stored, the private one is a
    # permanent skip in the log — so nothing new is fetched.
    requests: list = []
    counts = asyncio.run(run_crawl(ids, conn, _client(requests), skip_log_path=skip_log))

    assert counts["crawled"] == 0
    assert counts["skipped"] == 2
    assert requests == []
    # No duplicate rows crept in.
    assert conn.execute("SELECT COUNT(*) FROM seed_profiles").fetchone()[0] == 1
