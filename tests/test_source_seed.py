"""Seed sourcing (Goal 3.1): the stratification is pure and testable, and a full
pass fills every stratum, filters non-public IDs, and spends one owned-games call
per public candidate. All against MockTransport — no live key."""

import asyncio
import random

import httpx

from b4cklog.steam import SteamClient
from b4cklog.steam.models import OwnedGame
from pipeline import source_seed
from pipeline.source_seed import (
    ShapeFeatures,
    SourcingRun,
    _load_checkpoint,
    _save_checkpoint,
    classify,
    default_bins,
    shape_of,
    source_seed as run_sourcing,
)


# --- classify: each observable shape routes to its own stratum ---

def _shape(owned, played, hours):
    return ShapeFeatures(games_owned=owned, games_played=played, total_hours=hours)


def test_classify_concentrated_is_huge_hours_mostly_untouched():
    assert classify(_shape(owned=100, played=6, hours=1005.0)) == "concentrated"


def test_classify_focused_deep_is_few_games_deep_wells():
    assert classify(_shape(owned=10, played=3, hours=300.0)) == "focused_deep"


def test_classify_broad_engaged_is_large_and_played():
    assert classify(_shape(owned=200, played=150, hours=450.0)) == "broad_engaged"


def test_classify_broad_light_is_large_but_barely_played():
    assert classify(_shape(owned=200, played=50, hours=50.0)) == "broad_light"


def test_classify_thin_is_small_and_untouched():
    assert classify(_shape(owned=10, played=2, hours=2.0)) == "thin"


def test_classify_modest_is_the_ordinary_middle():
    assert classify(_shape(owned=50, played=20, hours=40.0)) == "modest"


def test_shape_of_ignores_unplayed_games():
    games = (
        OwnedGame(1, "a", 6000),   # 100h
        OwnedGame(2, "b", 3000),   # 50h
        OwnedGame(3, "c", 0),      # owned, never launched
    )
    shape = shape_of(games)
    assert shape.games_owned == 3
    assert shape.games_played == 2
    assert shape.total_hours == 150.0


def test_default_bins_scale_to_target():
    bins = default_bins(300)
    assert set(bins) == {
        "concentrated", "focused_deep", "broad_engaged",
        "broad_light", "modest", "thin",
    }
    # Caps sum to more than the target (the slack that stops rare strata from
    # stalling the run), and the common middle is allowed more than the fringe.
    assert sum(b.cap for b in bins.values()) > 300
    assert bins["modest"].cap > bins["thin"].cap


# --- a full sourcing pass against a synthetic population ---

# One representative library per shape class, so any random batch contains all
# six and the small strata fill quickly. Keyed by int(steam_id) % 6.
# Every shape has >= MIN_PLAYED_GAMES played, so the engagement gate passes them
# and the binning logic is what's under test.
_LIBRARIES = {
    0: [OwnedGame(0, "big", 60000)] + [OwnedGame(i, "s", 60) for i in range(1, 6)]
        + [OwnedGame(i, "u", 0) for i in range(6, 100)],          # concentrated
    1: [OwnedGame(i, "deep", 6000) for i in range(5)]
        + [OwnedGame(i, "u", 0) for i in range(5, 12)],           # focused_deep
    2: [OwnedGame(i, "p", 180) for i in range(150)]
        + [OwnedGame(i, "u", 0) for i in range(150, 200)],        # broad_engaged
    3: [OwnedGame(i, "p", 60) for i in range(50)]
        + [OwnedGame(i, "u", 0) for i in range(50, 200)],         # broad_light
    4: [OwnedGame(i, "p", 120) for i in range(20)]
        + [OwnedGame(i, "u", 0) for i in range(20, 50)],          # modest
    5: [OwnedGame(i, "a", 60) for i in range(5)]
        + [OwnedGame(i, "u", 0) for i in range(5, 10)],           # thin
}


def _owned_games_json(steam_id: str) -> dict:
    games = _LIBRARIES[int(steam_id) % 6]
    return {
        "response": {
            "game_count": len(games),
            "games": [
                {"appid": g.app_id, "name": g.name, "playtime_forever": g.playtime_minutes}
                for g in games
            ],
        }
    }


def _population_handler(private_ids: set[str] | None = None):
    """Every summaried ID is public except a marked few (games hidden)."""
    private_ids = private_ids or set()
    owned_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = request.url.params
        if path.endswith("GetPlayerSummaries/v2/"):
            ids = params["steamids"].split(",")
            players = [
                {"steamid": sid, "personaname": None, "communityvisibilitystate": 3}
                for sid in ids
            ]
            return httpx.Response(200, json={"response": {"players": players}})
        if path.endswith("GetOwnedGames/v1/"):
            sid = params["steamid"]
            owned_calls.append(sid)
            if sid in private_ids:
                # public community profile, hidden game details: no `games` key
                return httpx.Response(200, json={"response": {}})
            return httpx.Response(200, json=_owned_games_json(sid))
        raise AssertionError(f"unexpected path {path}")

    return handler, owned_calls


def _client(handler) -> SteamClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://api.steampowered.com")
    return SteamClient("test-key", client=http)


def test_full_pass_collects_the_target_without_exceeding_caps():
    handler, owned_calls = _population_handler()
    client = _client(handler)
    run = asyncio.run(
        run_sourcing(client, target=30, rng=random.Random(1), max_candidates=10_000)
    )

    # The run stops once the target is met (checked per batch, so the last batch
    # may overshoot), and never lets a stratum pass its cap.
    assert len(run.accepted) >= 30
    assert not run.stopped_early
    for b in run.bins.values():
        assert len(b.members) <= b.cap
    # One owned-games call per public candidate examined — the cheap-filter buy.
    assert run.public_found == len(owned_calls)


# --- resume: checkpoint round-trips, and a completed run does no work ---

def test_checkpoint_round_trips(tmp_path):
    run = SourcingRun(bins=default_bins(12), candidates_sampled=3400, public_found=900, games_hidden=800)
    run.bins["modest"].members.extend(["a", "b"])
    run.bins["thin"].members.append("c")

    path = tmp_path / "ckpt.json"
    _save_checkpoint(run, path)
    loaded = _load_checkpoint(12, path)

    assert loaded.candidates_sampled == 3400
    assert loaded.public_found == 900
    assert set(loaded.accepted) == {"a", "b", "c"}
    assert loaded.bins["modest"].members == ["a", "b"]


def test_load_checkpoint_absent_returns_none(tmp_path):
    assert _load_checkpoint(12, tmp_path / "nope.json") is None


def test_resume_from_completed_run_does_no_work():
    # A resumed run already past its target must return without a single call.
    def handler(request):
        raise AssertionError("no request should be made when the run is complete")

    run = SourcingRun(bins=default_bins(6))
    for b in run.bins.values():
        b.members.extend(str(i) for i in range(b.cap))  # fill every stratum to cap

    result = asyncio.run(run_sourcing(_client(handler), target=6, run=run))
    assert result.candidates_sampled == 0
    assert len(result.accepted) >= 6


def test_thin_libraries_are_gated_out_not_seeded():
    # Every candidate has only 2 played games — below MIN_PLAYED_GAMES — so none
    # may enter the seed; they're counted as too_thin, and the run exhausts its
    # ceiling without accepting anyone.
    thin_games = [OwnedGame(i, "p", 60) for i in range(2)] + [OwnedGame(i, "u", 0) for i in range(2, 20)]

    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        if path.endswith("GetPlayerSummaries/v2/"):
            players = [
                {"steamid": sid, "personaname": None, "communityvisibilitystate": 3}
                for sid in params["steamids"].split(",")
            ]
            return httpx.Response(200, json={"response": {"players": players}})
        return httpx.Response(200, json={"response": {"game_count": len(thin_games), "games": [
            {"appid": g.app_id, "name": g.name, "playtime_forever": g.playtime_minutes} for g in thin_games]}})

    run = asyncio.run(
        run_sourcing(_client(handler), target=12, rng=random.Random(1), max_candidates=200)
    )
    assert run.accepted == []
    assert run.too_thin > 0
    assert run.stopped_early and not run.rate_limit_aborted


def test_sustained_rate_limit_aborts_without_crashing(monkeypatch):
    # Owned-games always 429s: the run must abort gracefully (checkpointable),
    # never crash. Stub sleep so backoff doesn't actually wait.
    async def instant(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("GetPlayerSummaries/v2/"):
            players = [
                {"steamid": sid, "personaname": None, "communityvisibilitystate": 3}
                for sid in request.url.params["steamids"].split(",")
            ]
            return httpx.Response(200, json={"response": {"players": players}})
        return httpx.Response(429, text="slow down")  # every owned-games call

    run = asyncio.run(
        run_sourcing(_client(handler), target=12, rng=random.Random(1), max_candidates=100_000)
    )
    assert run.rate_limit_aborted
    assert run.stopped_early
    assert run.accepted == []
    assert run.rate_limited > 0


def test_transient_server_errors_do_not_crash_the_run(monkeypatch):
    # The real incident this guards against: an uncaught 502 from Steam
    # crashed a live sourcing run partway through, leaking the API key into
    # the crash traceback (httpx.HTTPStatusError embeds the full request URL;
    # fixed at the source in SteamClient._get). Owned-games always 502s here;
    # the run must survive it — abort gracefully, not crash the process.
    async def instant(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("GetPlayerSummaries/v2/"):
            players = [
                {"steamid": sid, "personaname": None, "communityvisibilitystate": 3}
                for sid in request.url.params["steamids"].split(",")
            ]
            return httpx.Response(200, json={"response": {"players": players}})
        return httpx.Response(502, text="bad gateway")  # every owned-games call

    run = asyncio.run(
        run_sourcing(_client(handler), target=12, rng=random.Random(1), max_candidates=100_000)
    )
    assert run.rate_limit_aborted
    assert run.stopped_early
    assert run.accepted == []


def test_games_hidden_profiles_are_counted_not_accepted():
    # Force a batch of IDs whose games are all hidden; none may be accepted, and
    # the run must give up at the candidate ceiling rather than loop forever.
    handler, _ = _population_handler(private_ids={str(source_seed.STEAM64_BASE + n) for n in range(1, 5_000)})

    class _AllHidden(random.Random):
        def randint(self, a, b):  # every drawn account is in the hidden set
            return super().randint(1, 4_999)

    client = _client(handler)
    run = asyncio.run(
        run_sourcing(client, target=12, rng=_AllHidden(0), max_candidates=300)
    )
    assert run.accepted == []
    assert run.games_hidden > 0
    assert run.stopped_early
