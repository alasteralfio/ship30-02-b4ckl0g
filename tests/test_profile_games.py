"""Game profiling (Goals 5.1/5.2): the pure axis-mapping functions, and the
end-to-end crawl against a mocked keyless client — a game profiles cleanly
with provenance, a delisted app and a non-game are skipped and logged, a
game with no achievements never gets a fake completability, and a re-run is
idempotent. No live network."""

import asyncio
import json

import httpx
import pytest

from b4cklog import store
from b4cklog.steam import AchievementPercent, GameDataClient
from pipeline import profile_games as pg
from pipeline.profile_games import profile_games as run_profile


@pytest.fixture
def conn():
    c = store.connect(":memory:")
    store.init_db(c)
    yield c
    c.close()


# --- pure mapping functions ---


def test_completability_is_mean_unlock_rate():
    pcts = [AchievementPercent("a", 80.0), AchievementPercent("b", 20.0)]
    assert pg._completability(pcts) == pytest.approx(0.5)


def test_completability_is_none_without_achievements():
    assert pg._completability(None) is None
    assert pg._completability([]) is None


def test_length_estimate_is_none_with_no_seed_playtime():
    assert pg._length_estimate([]) == (None, None)


def test_length_estimate_reads_median_and_upper_tail():
    minutes = [60, 120, 180, 240, 600]  # hours: 1, 2, 3, 4, 10
    beat, complete = pg._length_estimate(minutes)
    assert beat == pytest.approx(3.0)  # median
    assert complete == pytest.approx(4.0)  # index int(0.75*5)=3 -> the 4th value
    assert complete > beat  # the tail estimate is never below the median


def test_is_finite_none_without_signal():
    assert pg._is_finite((), ()) is None


def test_is_finite_false_for_mmo_genre():
    assert pg._is_finite(("Massively Multiplayer",), ("Single-player",)) is False


def test_is_finite_false_for_free_to_play_multiplayer_only():
    assert pg._is_finite(("Free To Play", "Action"), ("Multi-player",)) is False


def test_is_finite_false_for_free_to_play_even_with_single_player_missions():
    # Regression: Warframe and Destiny 2 both ship solo-capable missions on
    # top of an otherwise endless live-service loop, and both carry the
    # Single-player category — genre alone must still win.
    assert pg._is_finite(("Action", "RPG", "Free To Play"), ("Single-player", "Multi-player")) is False


def test_is_finite_true_for_single_player_games():
    assert pg._is_finite(("Adventure",), ("Single-player",)) is True


def test_is_finite_true_even_with_heavy_seed_playtime_outliers():
    # A completionist/replayer/idler exists for nearly any popular finite
    # game — playtime shape must never override a clean Single-player tag
    # (this regressed once: Elden Ring and Stardew Valley both came back
    # "endless" under the old playtime-outlier heuristic).
    assert pg._is_finite(("RPG",), ("Single-player",)) is True


def test_progression_style_matches_first_genre_bucket():
    assert pg._progression_style(("Massively Multiplayer",)) == "grinding"
    assert pg._progression_style(("RPG",)) == "exploration_narrative"
    assert pg._progression_style(("Action",)) == "skill_mastery"
    assert pg._progression_style(("Casual",)) is None


def test_content_shape_from_genre_then_from_length():
    assert pg._content_shape(("RPG",), None) == "sprawling"
    assert pg._content_shape(("Puzzle",), None) == "focused"
    assert pg._content_shape((), 20.0) == "sprawling"
    assert pg._content_shape((), 5.0) == "focused"
    assert pg._content_shape((), None) is None


# --- end-to-end crawl against a mocked keyless client ---


def _appdetails(app_id: int, **overrides) -> dict:
    data = {
        "name": f"Game {app_id}",
        "type": "game",
        "genres": [{"id": "1", "description": "Action"}],
        "categories": [{"id": 2, "description": "Single-player"}],
        "achievements": {"total": 2},
        **overrides,
    }
    return {"success": True, "data": data}


def _client(handler) -> GameDataClient:
    return GameDataClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        min_request_interval=0.0,
    )


def test_game_with_achievements_profiles_with_provenance(conn):
    def handler(request: httpx.Request) -> httpx.Response:
        if "appdetails" in str(request.url):
            return httpx.Response(200, json={"620": _appdetails(620)})
        return httpx.Response(
            200,
            json={"achievementpercentages": {"achievements": [
                {"name": "a", "percent": "80.0"}, {"name": "b", "percent": "40.0"},
            ]}},
        )

    counts = asyncio.run(run_profile([620], conn, _client(handler)))

    assert counts["profiled"] == 1
    profile = store.get_game_profile(conn, 620)
    assert profile["name"] == "Game 620"
    assert profile["has_achievements"] == 1
    assert profile["completability"] == pytest.approx(0.6)
    assert profile["provenance"]["completability"] == {
        "source": "global_achievement_pct", "is_estimate": False,
    }
    assert profile["provenance"]["is_finite"]["is_estimate"] is True


def test_game_with_no_achievements_gets_no_fake_completability(conn):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"440": _appdetails(440, achievements=None)})

    counts = asyncio.run(run_profile([440], conn, _client(handler)))

    assert counts["profiled"] == 1
    assert counts["no_achievements"] == 1
    profile = store.get_game_profile(conn, 440)
    assert profile["has_achievements"] == 0
    assert profile["completability"] is None
    assert "completability" not in profile["provenance"]


def test_delisted_app_is_skipped_and_logged(conn, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"9999999": {"success": False}})

    skip_log = tmp_path / "skips.jsonl"
    counts = asyncio.run(run_profile([9999999], conn, _client(handler), skip_log_path=skip_log))

    assert counts["delisted"] == 1
    assert store.get_game_profile(conn, 9999999) is None
    logged = json.loads(skip_log.read_text().splitlines()[0])
    assert logged["reason"] == "delisted"


def test_non_game_type_is_skipped_and_logged(conn, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"123": _appdetails(123, type="dlc")})

    skip_log = tmp_path / "skips.jsonl"
    counts = asyncio.run(run_profile([123], conn, _client(handler), skip_log_path=skip_log))

    assert counts["delisted"] == 1
    assert store.get_game_profile(conn, 123) is None
    logged = json.loads(skip_log.read_text().splitlines()[0])
    assert logged["reason"] == "not_a_game"


def test_refresh_derived_fields_fixes_is_finite_without_touching_achievements(conn):
    # Profile once under a handler that returns achievements, simulating data
    # built while the old playtime-based `_is_finite` was still in place by
    # hand-writing a wrong is_finite, then confirm refresh corrects it using
    # only a fresh appdetails call — never re-touching completability.
    def handler(request: httpx.Request) -> httpx.Response:
        if "appdetails" in str(request.url):
            return httpx.Response(200, json={"620": _appdetails(620)})
        return httpx.Response(
            200,
            json={"achievementpercentages": {"achievements": [{"name": "a", "percent": "50.0"}]}},
        )

    asyncio.run(run_profile([620], conn, _client(handler)))
    with conn:
        conn.execute("UPDATE game_profiles SET is_finite = 0 WHERE app_id = 620")
    assert store.get_game_profile(conn, 620)["is_finite"] == 0

    from pipeline.profile_games import refresh_derived_fields

    def appdetails_only_handler(request: httpx.Request) -> httpx.Response:
        assert "appdetails" in str(request.url)  # never calls the achievement endpoint
        return httpx.Response(200, json={"620": _appdetails(620)})

    counts = asyncio.run(refresh_derived_fields([620], conn, _client(appdetails_only_handler)))
    profile = store.get_game_profile(conn, 620)

    assert counts["updated"] == 1
    assert bool(profile["is_finite"]) is True  # category is Single-player -> corrected
    assert profile["completability"] == pytest.approx(0.5)  # untouched


def test_rerun_skips_already_profiled_games(conn):
    def handler(request: httpx.Request) -> httpx.Response:
        if "appdetails" in str(request.url):
            return httpx.Response(200, json={"620": _appdetails(620)})
        return httpx.Response(200, json={"achievementpercentages": {"achievements": []}})

    first = asyncio.run(run_profile([620], conn, _client(handler)))
    second = asyncio.run(run_profile([620], conn, _client(handler)))

    assert first["profiled"] == 1
    assert second["profiled"] == 0
    assert second["skipped"] == 1
