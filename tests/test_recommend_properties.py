"""Game-property matching (Goal 6.1): a game's fit is blended across a
player's continuous archetype mix, each archetype's ideal shape matches its
PROJECT.md description, and a game with no data reads as neutral with zero
coverage rather than a confident-looking guess."""

from b4cklog.placement import Placement, Subdivision
from b4cklog.recommend.properties import score_game_properties

_EMPTY_GAME = {
    "is_finite": None,
    "completability": None,
    "hours_to_beat": None,
    "hours_to_complete": None,
    "progression_style": None,
    "content_shape": None,
}


def _game(**overrides) -> dict:
    return {**_EMPTY_GAME, **overrides}


def _placement(responsibilities: dict[str, float], subdivision: Subdivision | None = None) -> Placement:
    ranked = sorted(responsibilities.items(), key=lambda kv: -kv[1])
    primary, primary_p = ranked[0]
    secondary, secondary_p = ranked[1] if len(ranked) > 1 else (None, None)
    return Placement(
        coordinates=(0.0, 0.0, 0.0),
        responsibilities=responsibilities,
        primary=primary,
        primary_probability=primary_p,
        secondary=secondary,
        secondary_probability=secondary_p,
        outlier=False,
        soft_label="",
        subdivision=subdivision,
    )


def test_game_with_no_data_is_neutral_with_no_coverage():
    fit = score_game_properties(_placement({"Dabbler": 1.0}), _EMPTY_GAME)
    assert fit.score == 0.5
    assert fit.coverage == 0.0


def test_breadth_completionist_prefers_short_finite_completable_games():
    placement = _placement(
        {"Completionist": 1.0},
        subdivision=Subdivision(name="Breadth Completionist", detail="", confident=True),
    )
    short = _game(is_finite=True, completability=0.9, content_shape="focused")
    long = _game(is_finite=True, completability=0.9, content_shape="sprawling")
    assert score_game_properties(placement, short).score > score_game_properties(placement, long).score


def test_depth_completionist_prefers_long_finite_completable_games():
    placement = _placement(
        {"Completionist": 1.0},
        subdivision=Subdivision(name="Depth Completionist", detail="", confident=True),
    )
    short = _game(is_finite=True, completability=0.9, content_shape="focused")
    long = _game(is_finite=True, completability=0.9, content_shape="sprawling")
    assert score_game_properties(placement, long).score > score_game_properties(placement, short).score


def test_completionist_penalizes_endless_uncompletable_games():
    placement = _placement(
        {"Completionist": 1.0},
        subdivision=Subdivision(name="Breadth Completionist", detail="", confident=True),
    )
    finishable = _game(is_finite=True, completability=0.9, content_shape="focused")
    endless = _game(is_finite=False, completability=0.1, content_shape="sprawling")
    assert score_game_properties(placement, finishable).score > score_game_properties(placement, endless).score


def test_dabbler_prefers_short_low_commitment_games():
    placement = _placement({"Dabbler": 1.0})
    for_dabbler = _game(is_finite=True, content_shape="focused", progression_style="exploration_narrative")
    not_for_dabbler = _game(is_finite=False, content_shape="sprawling", progression_style="grinding")
    assert score_game_properties(placement, for_dabbler).score > score_game_properties(placement, not_for_dabbler).score


def test_obsessive_prefers_endless_grindy_systems_games():
    placement = _placement({"Obsessive": 1.0})
    for_obsessive = _game(is_finite=False, progression_style="grinding", content_shape="sprawling")
    not_for_obsessive = _game(is_finite=True, progression_style="exploration_narrative", content_shape="focused")
    assert (
        score_game_properties(placement, for_obsessive).score
        > score_game_properties(placement, not_for_obsessive).score
    )


def test_enthusiast_score_barely_moves_with_length_or_completability():
    placement = _placement({"Enthusiast": 1.0})
    long_uncompletable = _game(is_finite=False, completability=0.05, hours_to_complete=200.0)
    short_completable = _game(is_finite=True, completability=0.95, hours_to_complete=3.0)
    # Neither length nor completability is a field Enthusiast reads at all —
    # both fall back to the same neutral read.
    assert score_game_properties(placement, long_uncompletable).score == score_game_properties(
        placement, short_completable
    ).score


def test_enthusiast_gets_a_mild_nod_for_sprawling_content():
    placement = _placement({"Enthusiast": 1.0})
    sprawling = _game(content_shape="sprawling")
    focused = _game(content_shape="focused")
    assert score_game_properties(placement, sprawling).score > score_game_properties(placement, focused).score


def test_curator_prefers_finite_games_with_a_deep_completion_tail():
    placement = _placement({"Curator": 1.0})
    roguelike_shape = _game(is_finite=True, hours_to_beat=5.0, hours_to_complete=40.0)
    linear_shape = _game(is_finite=True, hours_to_beat=10.0, hours_to_complete=11.0)
    assert (
        score_game_properties(placement, roguelike_shape).score
        > score_game_properties(placement, linear_shape).score
    )


def test_unclassified_component_votes_neutral_and_uncovered():
    placement = _placement({"Unclassified-0": 1.0})
    game = _game(is_finite=True, completability=0.9, content_shape="focused")
    fit = score_game_properties(placement, game)
    assert fit.score == 0.5
    assert fit.coverage == 0.0


def test_blend_is_a_real_weighted_average_of_the_mix():
    # 60% Dabbler / 40% Obsessive on a game that's great for Dabbler and
    # terrible for Obsessive should land nearer the Dabbler-only score than
    # a 50/50 split would, not snapped to either pure read.
    game = _game(is_finite=True, content_shape="focused", progression_style="exploration_narrative")
    dabbler_only = score_game_properties(_placement({"Dabbler": 1.0}), game).score
    mixed = score_game_properties(_placement({"Dabbler": 0.6, "Obsessive": 0.4}), game).score
    obsessive_only = score_game_properties(_placement({"Obsessive": 1.0}), game).score
    assert obsessive_only < mixed < dabbler_only


def test_coverage_reflects_real_fields_across_the_whole_mix():
    # Dabbler's fields (content_shape, progression_style) are present; Curator
    # reads only is_finite and the hours pair, both absent here — so half the
    # responsibility weight has no real backing.
    placement = _placement({"Dabbler": 0.5, "Curator": 0.5})
    game = _game(content_shape="focused", progression_style="exploration_narrative")
    fit = score_game_properties(placement, game)
    assert fit.coverage == 0.5
