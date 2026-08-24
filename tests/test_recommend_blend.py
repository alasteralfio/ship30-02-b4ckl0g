"""Blend and split (Goal 6.3): the two engines combine by blend weight, the
honest catch fires only when there's real evidence for it, the two
recommendation lists stay separated by ownership, an empty or unprofiled
backlog says so instead of going quietly empty, and the blend genuinely shifts
with neighborhood density."""

import pytest

from b4cklog import store
from b4cklog.placement import Placement
from b4cklog.placement import model as placement_model
from b4cklog.recommend.blend import (
    MAX_SIMILARITY_WEIGHT,
    _build_caveat,
    _combine,
    recommend,
)
from b4cklog.recommend.properties import PropertyFit
from b4cklog.recommend.similarity import SimilarityFit
from b4cklog.steam import Library, OwnedGame


def _placement(responsibilities: dict[str, float], coordinates=(0.0, 0.0, 0.0)) -> Placement:
    ranked = sorted(responsibilities.items(), key=lambda kv: -kv[1])
    primary, primary_p = ranked[0]
    secondary, secondary_p = ranked[1] if len(ranked) > 1 else (None, None)
    return Placement(
        coordinates=coordinates,
        responsibilities=responsibilities,
        primary=primary,
        primary_probability=primary_p,
        secondary=secondary,
        secondary_probability=secondary_p,
        outlier=False,
        soft_label="",
        subdivision=None,
    )


def _game(**overrides) -> dict:
    base = {
        "app_id": 1, "name": "Test Game", "is_finite": None, "completability": None,
        "hours_to_beat": None, "hours_to_complete": None,
        "progression_style": None, "content_shape": None,
    }
    return {**base, **overrides}


def _single_component_model(profiles: list[dict]):
    # GaussianMixture needs at least 2 samples to fit at all; a lone synthetic
    # profile is duplicated rather than asking every call site to pad its list.
    if len(profiles) == 1:
        profiles = profiles * 2
    return placement_model.fit(profiles, component_range=range(1, 2))


# --- _combine / _build_caveat: pure combine logic ---


def test_combine_blends_property_and_similarity_by_weight():
    property_fit = PropertyFit(score=0.2, coverage=1.0)
    similarity_fit = SimilarityFit(score=0.8, weighted_hours=0.0, engaged_neighbors=0)
    rec = _combine(_game(), property_fit, similarity_fit, blend_weight=0.5)
    assert rec.score == pytest.approx(0.5)


def test_combine_at_zero_blend_weight_is_pure_property_score():
    property_fit = PropertyFit(score=0.3, coverage=1.0)
    similarity_fit = SimilarityFit(score=0.9, weighted_hours=0.0, engaged_neighbors=0)
    rec = _combine(_game(), property_fit, similarity_fit, blend_weight=0.0)
    assert rec.score == pytest.approx(0.3)


def test_caveat_fires_when_the_cluster_typically_doesnt_finish_it():
    similarity_fit = SimilarityFit(score=0.5, weighted_hours=12.0, engaged_neighbors=5)
    caveat = _build_caveat(_game(hours_to_complete=80.0), similarity_fit)
    assert caveat is not None
    assert "80" in caveat
    assert "12" in caveat


def test_caveat_is_silent_with_too_little_neighbor_evidence():
    similarity_fit = SimilarityFit(score=0.5, weighted_hours=2.0, engaged_neighbors=1)
    assert _build_caveat(_game(hours_to_complete=80.0), similarity_fit) is None


def test_caveat_is_silent_when_the_cluster_actually_finishes_it():
    similarity_fit = SimilarityFit(score=0.5, weighted_hours=9.0, engaged_neighbors=5)
    assert _build_caveat(_game(hours_to_complete=10.0), similarity_fit) is None


def test_caveat_is_silent_with_no_length_estimate():
    similarity_fit = SimilarityFit(score=0.5, weighted_hours=1.0, engaged_neighbors=5)
    assert _build_caveat(_game(hours_to_complete=None), similarity_fit) is None


# --- recommend(): end to end against an in-memory store ---


@pytest.fixture
def conn():
    c = store.connect(":memory:")
    store.init_db(c)
    yield c
    c.close()


def _seed_profile(conn, steam_id, depth, completion, consistency):
    store.upsert_seed_profile(
        conn, steam_id,
        depth_breadth=depth, completion_drive=completion, commitment_consistency=consistency,
        shape_features={}, crawled_at="2026-08-19T00:00:00Z",
    )
    store.replace_seed_playtimes(conn, steam_id, {})


def _game_profile(conn, app_id, name, *, in_outside_pool=False, **fields):
    defaults = dict(name=name, has_achievements=True, in_outside_pool=in_outside_pool, profiled_at="now")
    store.upsert_game_profile(conn, app_id, **{**defaults, **fields})


def test_empty_backlog_gets_an_honest_note(conn):
    model = _single_component_model(
        [{"depth_breadth": 1.0, "completion_drive": 0.5, "commitment_consistency": 0.5}]
    )
    placement = _placement({"Enthusiast": 1.0})
    library = Library(
        steam_id="1", persona_name="test",
        games=(OwnedGame(app_id=10, name="Played Game", playtime_minutes=600),),
    )
    result = recommend(conn, model, placement, library)
    assert result.backlog == []
    assert "Nothing to recommend" in result.backlog_note


def test_unprofiled_backlog_games_get_an_honest_note_not_silently_dropped(conn):
    model = _single_component_model(
        [{"depth_breadth": 1.0, "completion_drive": 0.5, "commitment_consistency": 0.5}]
    )
    placement = _placement({"Enthusiast": 1.0})
    library = Library(
        steam_id="1", persona_name="test",
        games=(OwnedGame(app_id=999, name="Unprofiled Game", playtime_minutes=0),),
    )
    result = recommend(conn, model, placement, library)
    assert result.backlog == []
    assert "1 owned-but-unplayed" in result.backlog_note


def test_backlog_only_contains_owned_unplayed_games(conn):
    _game_profile(conn, 10, "Backlog Candidate", is_finite=True, completability=0.8, content_shape="focused")
    _game_profile(conn, 20, "Already Playing", is_finite=True, completability=0.8, content_shape="focused")
    model = _single_component_model(
        [{"depth_breadth": 1.0, "completion_drive": 0.5, "commitment_consistency": 0.5}]
    )
    placement = _placement({"Enthusiast": 1.0})
    library = Library(
        steam_id="1", persona_name="test",
        games=(
            OwnedGame(app_id=10, name="Backlog Candidate", playtime_minutes=0),
            OwnedGame(app_id=20, name="Already Playing", playtime_minutes=600),
        ),
    )
    result = recommend(conn, model, placement, library)
    assert [r.app_id for r in result.backlog] == [10]


def test_outside_list_excludes_owned_games(conn):
    _game_profile(conn, 30, "Owned Outside Pick", in_outside_pool=True)
    _game_profile(conn, 40, "Unowned Outside Pick", in_outside_pool=True)
    model = _single_component_model(
        [{"depth_breadth": 1.0, "completion_drive": 0.5, "commitment_consistency": 0.5}]
    )
    placement = _placement({"Enthusiast": 1.0})
    library = Library(
        steam_id="1", persona_name="test",
        games=(OwnedGame(app_id=30, name="Owned Outside Pick", playtime_minutes=600),),
    )
    result = recommend(conn, model, placement, library)
    assert [r.app_id for r in result.outside] == [40]


def test_blend_weight_grows_with_neighborhood_density(conn):
    # A visitor sitting on top of a dense little cluster should blend more
    # toward similarity than one sitting nowhere near the seed at all.
    for i in range(15):
        _seed_profile(conn, f"seed-{i}", 1.0 + i * 0.001, 0.5, 0.5)
    model = _single_component_model(store.all_seed_profiles(conn))
    library = Library(steam_id="1", persona_name="test", games=())

    dense_coords = tuple(model.scaler.transform([[1.0, 0.5, 0.5]])[0])
    dense_placement = _placement({"Enthusiast": 1.0}, coordinates=dense_coords)
    lonely_placement = _placement({"Enthusiast": 1.0}, coordinates=(50.0, 50.0, 50.0))

    dense_result = recommend(conn, model, dense_placement, library)
    lonely_result = recommend(conn, model, lonely_placement, library)

    assert lonely_result.blend_weight < dense_result.blend_weight
    assert dense_result.blend_weight <= MAX_SIMILARITY_WEIGHT
