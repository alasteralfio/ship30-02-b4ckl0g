"""Building a report from already-fetched player data (Goals 7.1, 7.3): a
wholly unplayed library reads as no signal rather than a guessed placement, a
thin library still gets a real read but flagged low-confidence, and a normal
library gets a full report. Synthetic data and an in-memory store throughout
— no network, no live key."""

import pytest

from b4cklog import store
from b4cklog.placement import model as placement_model
from b4cklog.steam import Library, OwnedGame
from b4cklog.web.report import MIN_GAMES_FOR_CONFIDENT_READ, NoSignal, Report, build_report


@pytest.fixture
def conn():
    c = store.connect(":memory:")
    store.init_db(c)
    yield c
    c.close()


@pytest.fixture
def model():
    profiles = [
        {"depth_breadth": 1.0, "completion_drive": 0.5, "commitment_consistency": 0.5},
        {"depth_breadth": 1.1, "completion_drive": 0.5, "commitment_consistency": 0.5},
    ]
    return placement_model.fit(profiles, component_range=range(1, 2))


def _library(games: tuple[OwnedGame, ...]) -> Library:
    return Library(steam_id="1", persona_name="tester", games=games)


def test_wholly_unplayed_library_is_no_signal(conn, model):
    library = _library((
        OwnedGame(app_id=1, name="A", playtime_minutes=0),
        OwnedGame(app_id=2, name="B", playtime_minutes=0),
    ))
    result = build_report(library, {}, model, conn)
    assert isinstance(result, NoSignal)
    assert result.games_owned == 2


def test_thin_library_still_gets_a_read_but_flagged(conn, model):
    games = tuple(
        OwnedGame(app_id=i, name=f"Game {i}", playtime_minutes=60)
        for i in range(MIN_GAMES_FOR_CONFIDENT_READ - 1)
    )
    library = _library(games)
    result = build_report(library, {}, model, conn)
    assert isinstance(result, Report)
    assert result.low_confidence
    assert result.placement is not None


def test_normal_library_gets_a_confident_report(conn, model):
    games = tuple(
        OwnedGame(app_id=i, name=f"Game {i}", playtime_minutes=600)
        for i in range(MIN_GAMES_FOR_CONFIDENT_READ + 5)
    )
    library = _library(games)
    result = build_report(library, {}, model, conn)
    assert isinstance(result, Report)
    assert not result.low_confidence
    assert result.games_played == MIN_GAMES_FOR_CONFIDENT_READ + 5
    assert result.recommendations is not None
