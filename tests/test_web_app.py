"""The request flow end to end (Goals 7.1-7.4, Checkpoint 9): a public
profile renders a full report with a well-formed SVG; a private profile, an
unknown ID, an unplayed library, and a Steam-side failure each render their
own honest page instead of a 500; and the per-IP rate limiter returns a clear
429 past its threshold without the request ever reaching the fake Steam
client. Every Steam call is a fake injected via dependency override — no
network, no live key."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from b4cklog import store
from b4cklog.placement import model as placement_model
from b4cklog.steam import (
    Library,
    OwnedGame,
    PrivateProfile,
    SteamError,
    UnknownSteamID,
    UnreadableReason,
)
import b4cklog.web.app as app_module
from b4cklog.web.rate_limit import RateLimiter, TTLCache


class _FakeSteamClient:
    """Duck-types `SteamClient` far enough for `app.profile` to use it: a
    canned `read_library` result and empty achievements."""

    def __init__(self, result):
        self._result = result

    async def read_library(self, steam_id: str):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    async def sample_achievements(self, library):
        return {}


@pytest.fixture
def model():
    profiles = [
        {"depth_breadth": 1.0, "completion_drive": 0.5, "commitment_consistency": 0.5},
        {"depth_breadth": 1.1, "completion_drive": 0.5, "commitment_consistency": 0.5},
    ]
    return placement_model.fit(profiles, component_range=range(1, 2))


@pytest.fixture
def conn():
    # TestClient runs the ASGI app on a worker thread via an AnyIO portal,
    # distinct from the thread this fixture runs on; sqlite3 connections are
    # bound to their creating thread by default, so a plain `store.connect`
    # here would fail with "created in a thread" the moment the overridden
    # `get_conn` dependency hands this same connection to the route on the
    # portal's thread. `get_conn` itself doesn't have this problem in
    # production (it creates and uses the connection in the same async
    # context) — this is purely a test-harness artifact of sharing one
    # in-memory connection across the fixture and the portal thread.
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    store.init_db(c)
    store.upsert_game_profile(
        c, 10, name="Backlog Pick", has_achievements=True,
        is_finite=True, completability=0.8, content_shape="focused",
    )
    store.upsert_game_profile(
        c, 20, name="Discovery Pick", has_achievements=True, in_outside_pool=True,
        is_finite=True, completability=0.7, content_shape="sprawling",
    )
    yield c
    c.close()


@pytest.fixture
def client(model, conn):
    app_module.app.dependency_overrides[app_module.get_model] = lambda: model
    app_module.app.dependency_overrides[app_module.get_conn] = lambda: conn
    # Fresh, deterministic rate limiter and cache per test — the production
    # singletons persist across requests by design, which would otherwise
    # leak state between tests.
    app_module.rate_limiter = RateLimiter(max_requests=2, window_seconds=60.0)
    app_module.lookup_cache = TTLCache(ttl_seconds=60.0)
    # Deliberately not `with TestClient(...) as c:` — that would run the real
    # `lifespan()` and load `data/placement_model.pkl` off disk, coupling
    # this offline unit suite to a pipeline-built artifact it doesn't need
    # (every dependency the routes actually use is overridden below).
    yield TestClient(app_module.app)
    app_module.app.dependency_overrides.clear()


def _use_fake_client(result_or_exception):
    app_module.app.dependency_overrides[app_module.get_steam_client] = (
        lambda: _FakeSteamClient(result_or_exception)
    )


def test_index_shows_the_lookup_form(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "steam_id" in response.text


def test_public_profile_renders_a_full_report(client):
    library = Library(
        steam_id="1", persona_name="Tester",
        games=(
            *(OwnedGame(app_id=100 + i, name=f"Played {i}", playtime_minutes=600) for i in range(6)),
            OwnedGame(app_id=10, name="Backlog Pick", playtime_minutes=0),
        ),
    )
    _use_fake_client(library)
    response = client.get("/profile", params={"steam_id": "1"})
    assert response.status_code == 200
    assert "<svg" in response.text
    assert "You play most like" in response.text
    assert "Backlog Pick" in response.text
    assert "Discovery Pick" in response.text


def test_private_profile_renders_the_honest_page_not_a_500(client):
    _use_fake_client(PrivateProfile("2", "Private Person", UnreadableReason.PROFILE_PRIVATE))
    response = client.get("/profile", params={"steam_id": "2"})
    assert response.status_code == 200
    assert "Can't read" in response.text


def test_unplayed_library_renders_the_no_signal_page(client):
    library = Library(
        steam_id="3", persona_name="Newbie",
        games=(OwnedGame(app_id=1, name="Unplayed", playtime_minutes=0),),
    )
    _use_fake_client(library)
    response = client.get("/profile", params={"steam_id": "3"})
    assert response.status_code == 200
    assert "nothing to read yet" in response.text.lower() or "hasn" in response.text.lower() or "no behaviour" in response.text.lower() or "isn't going to guess" in response.text.lower()


def test_unknown_steam_id_returns_404_not_500(client):
    _use_fake_client(UnknownSteamID("no such account"))
    response = client.get("/profile", params={"steam_id": "nonexistent"})
    assert response.status_code == 404


def test_steam_error_returns_502_not_500(client):
    _use_fake_client(SteamError("Steam kept failing"))
    response = client.get("/profile", params={"steam_id": "4"})
    assert response.status_code == 502


def test_rate_limiter_returns_429_past_its_threshold_without_calling_steam(client):
    library = Library(steam_id="5", persona_name="Fine", games=())

    class _CountingClient(_FakeSteamClient):
        calls = 0

        async def read_library(self, steam_id: str):
            _CountingClient.calls += 1
            return await super().read_library(steam_id)

    app_module.app.dependency_overrides[app_module.get_steam_client] = lambda: _CountingClient(library)

    first = client.get("/profile", params={"steam_id": "5"})
    second = client.get("/profile", params={"steam_id": "6"})
    third = client.get("/profile", params={"steam_id": "7"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert _CountingClient.calls == 2  # the third request never reached the fake Steam client
