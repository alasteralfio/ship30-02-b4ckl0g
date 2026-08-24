"""Player-similarity matching (Goal 6.2): neighbors are found in the model's
own standardized space and weighted by real distance, density saturates
rather than climbing unbounded, and game scores are a real weighted average of
engaged neighbors' time — not diluted by neighbors who never touched a game."""

import numpy as np
import pytest

from b4cklog.behaviour import Behaviour
from b4cklog.placement import model as placement_model
from b4cklog.placement.label import place
from b4cklog.recommend.similarity import (
    Neighbor,
    nearest_neighbors,
    neighborhood_density,
    score_similarity,
)


def _profile(steam_id: str, depth: float, completion: float, consistency: float) -> dict:
    return {
        "steam_id": steam_id,
        "depth_breadth": depth,
        "completion_drive": completion,
        "commitment_consistency": consistency,
    }


@pytest.fixture(scope="module")
def seed_and_model():
    # A tight cluster near (1, 0.8, 0.8) and a far one near (5, 0.1, 0.1) —
    # well-separated enough that "close" and "far" are unambiguous under
    # whatever standardization the fit produces.
    rng = np.random.default_rng(0)
    profiles = []
    for i in range(10):
        d, c, k = rng.normal([1.0, 0.8, 0.8], 0.05)
        profiles.append(_profile(f"close-{i}", d, float(np.clip(c, 0, 1)), float(np.clip(k, 0, 1))))
    for i in range(10):
        d, c, k = rng.normal([5.0, 0.1, 0.1], 0.05)
        profiles.append(_profile(f"far-{i}", d, float(np.clip(c, 0, 1)), float(np.clip(k, 0, 1))))
    model = placement_model.fit(profiles, component_range=range(2, 3))
    return profiles, model


# --- nearest_neighbors ---


def test_nearest_neighbors_favors_the_close_cluster(seed_and_model):
    profiles, model = seed_and_model
    behaviour = Behaviour(depth_breadth=1.0, completion_drive=0.8, commitment_consistency=0.8, shape={})
    placement = place(model, behaviour)
    neighbors = nearest_neighbors(model, placement, profiles, k=10)
    assert len(neighbors) == 10
    assert all(n.steam_id.startswith("close-") for n in neighbors)


def test_nearest_neighbors_weight_decays_with_distance(seed_and_model):
    profiles, model = seed_and_model
    behaviour = Behaviour(depth_breadth=1.0, completion_drive=0.8, commitment_consistency=0.8, shape={})
    placement = place(model, behaviour)
    neighbors = nearest_neighbors(model, placement, profiles, k=20)
    by_distance = sorted(neighbors, key=lambda n: n.distance)
    weights = [n.weight for n in by_distance]
    assert weights == sorted(weights, reverse=True)
    assert all(0.0 < w <= 1.0 for w in weights)


def test_nearest_neighbors_empty_seed_gives_no_neighbors(seed_and_model):
    _, model = seed_and_model
    behaviour = Behaviour(depth_breadth=1.0, completion_drive=0.8, commitment_consistency=0.8, shape={})
    placement = place(model, behaviour)
    assert nearest_neighbors(model, placement, []) == []


# --- neighborhood_density ---


def test_density_is_zero_with_no_neighbors():
    assert neighborhood_density([]) == 0.0


def test_density_grows_with_more_close_neighbors_but_saturates_below_one():
    few = [Neighbor(steam_id="a", distance=0.1, weight=0.95)]
    many = [Neighbor(steam_id=f"n{i}", distance=0.1, weight=0.95) for i in range(30)]
    assert neighborhood_density(few) < neighborhood_density(many) < 1.0


# --- score_similarity ---


def test_similarity_score_is_a_proximity_weighted_average_of_engaged_neighbors():
    close = Neighbor(steam_id="close", distance=0.0, weight=1.0)
    far = Neighbor(steam_id="far", distance=2.0, weight=0.1)
    playtimes = {"close": {100: 600}, "far": {100: 6000}}  # 10h vs 100h
    fits = score_similarity([close, far], playtimes, [100])
    expected = (1.0 * 10.0 + 0.1 * 100.0) / 1.1
    assert fits[100].weighted_hours == pytest.approx(expected)
    assert fits[100].engaged_neighbors == 2


def test_similarity_score_ignores_neighbors_who_never_played_it():
    played = Neighbor(steam_id="a", distance=0.0, weight=1.0)
    unplayed = Neighbor(steam_id="b", distance=0.0, weight=1.0)
    playtimes = {"a": {100: 600}, "b": {}}
    fits = score_similarity([played, unplayed], playtimes, [100])
    assert fits[100].engaged_neighbors == 1
    assert fits[100].weighted_hours == pytest.approx(10.0)


def test_similarity_score_with_no_engaged_neighbors_is_zero():
    neighbor = Neighbor(steam_id="a", distance=0.0, weight=1.0)
    fits = score_similarity([neighbor], {"a": {}}, [999])
    assert fits[999].score == 0.0
    assert fits[999].weighted_hours == 0.0
    assert fits[999].engaged_neighbors == 0


def test_similarity_score_normalizes_within_the_candidate_set():
    neighbor = Neighbor(steam_id="a", distance=0.0, weight=1.0)
    playtimes = {"a": {1: 600, 2: 1200}}  # 10h vs 20h; app 3 has no data at all
    fits = score_similarity([neighbor], playtimes, [1, 2, 3])
    assert fits[2].score == 1.0
    assert fits[1].score == pytest.approx(0.5)
    assert fits[3].score == 0.0
