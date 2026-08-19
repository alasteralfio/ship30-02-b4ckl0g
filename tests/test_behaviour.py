"""Behavioural reduction: the three axes point the right way, the library-shape
features read the split, and the edges dev-rules names hold without NaN or a
divide-by-zero — same input, same point every time.
"""

import math

import pytest

from b4cklog.behaviour import reduce_to_behaviour
from b4cklog.steam import AchievementStats, Library, OwnedGame


def _library(playtimes: list[int]) -> Library:
    games = tuple(
        OwnedGame(app_id=1000 + i, name=f"game-{i}", playtime_minutes=m)
        for i, m in enumerate(playtimes)
    )
    return Library(steam_id="p", persona_name=None, games=games)


def _achievements(rates: dict[int, tuple[int, int]]) -> dict[int, AchievementStats]:
    return {
        app_id: AchievementStats(app_id=app_id, unlocked=u, total=t)
        for app_id, (u, t) in rates.items()
    }


# --- direction of each axis ---

def test_breadth_scores_higher_than_depth():
    depth = reduce_to_behaviour(_library([1000, 5, 5]), {})
    breadth = reduce_to_behaviour(_library([100, 100, 100, 100, 100]), {})
    assert breadth.depth_breadth > depth.depth_breadth


def test_even_play_is_more_consistent_than_outlier_dominated():
    even = reduce_to_behaviour(_library([100, 100, 100]), {})
    outlier = reduce_to_behaviour(_library([1000, 10, 10]), {})
    assert even.commitment_consistency > outlier.commitment_consistency
    assert even.commitment_consistency == pytest.approx(1.0)  # Gini 0 on equal play


def test_completion_drive_is_mean_over_games_with_achievements():
    library = _library([500, 400])
    achievements = _achievements({1000: (45, 50), 1001: (48, 50)})
    behaviour = reduce_to_behaviour(library, achievements)
    assert behaviour.completion_drive == pytest.approx((0.9 + 0.96) / 2)


def test_split_library_reads_more_bimodal_than_an_even_one():
    split = reduce_to_behaviour(_library([5, 5, 5, 5, 6000, 6000, 6000, 6000]), {})
    even = reduce_to_behaviour(_library([100, 110, 90, 105, 95, 100, 100, 102]), {})
    assert split.shape["bimodality"] > even.shape["bimodality"]


# --- the edges dev-rules names ---

def test_empty_library_lands_at_the_origin_with_no_nan():
    behaviour = reduce_to_behaviour(_library([]), {})
    assert (behaviour.depth_breadth, behaviour.completion_drive, behaviour.commitment_consistency) == (0.0, 0.0, 0.0)
    assert behaviour.shape["games_owned"] == 0
    assert behaviour.shape["games_played"] == 0
    assert behaviour.shape["played_fraction"] == 0.0
    _assert_all_finite(behaviour)


def test_all_unplayed_library_has_no_signal_but_records_ownership():
    behaviour = reduce_to_behaviour(_library([0, 0, 0]), {})
    assert behaviour.depth_breadth == 0.0
    assert behaviour.shape["games_owned"] == 3
    assert behaviour.shape["games_played"] == 0
    _assert_all_finite(behaviour)


def test_one_enormous_outlier_is_pure_depth_and_holds():
    behaviour = reduce_to_behaviour(_library([48000]), {})
    assert behaviour.depth_breadth == pytest.approx(0.0)  # ln(1) effective game
    assert behaviour.commitment_consistency == pytest.approx(1.0)  # nothing to be uneven against
    assert behaviour.shape["bimodality"] == 0.0  # one game has no curve
    _assert_all_finite(behaviour)


def test_zero_achievements_gives_zero_completion_drive_not_nan():
    behaviour = reduce_to_behaviour(_library([300, 200, 100]), {})
    assert behaviour.completion_drive == 0.0
    _assert_all_finite(behaviour)


# --- determinism ---

def test_identical_input_yields_identical_output():
    library = _library([800, 300, 120, 40, 10, 5])
    achievements = _achievements({1000: (30, 40), 1002: (12, 60)})
    first = reduce_to_behaviour(library, achievements)
    second = reduce_to_behaviour(library, achievements)
    assert first == second


def test_game_order_does_not_change_the_point():
    forward = _library([800, 300, 120, 40, 10, 5])
    shuffled = _library([10, 800, 40, 5, 300, 120])
    assert reduce_to_behaviour(forward, {}) == reduce_to_behaviour(shuffled, {})


def _assert_all_finite(behaviour) -> None:
    for value in (
        behaviour.depth_breadth,
        behaviour.completion_drive,
        behaviour.commitment_consistency,
        behaviour.shape["bimodality"],
        behaviour.shape["played_fraction"],
    ):
        assert math.isfinite(value)
