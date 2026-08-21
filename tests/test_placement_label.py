"""Reading a placement off the frozen model (Goal 4.2): the soft label stays
continuous rather than snapping to the nearest archetype, the two subdivisions
fire on shape, and placement is deterministic. Pure logic and synthetic seed
data throughout — no live key needed."""

import numpy as np
import pytest

from b4cklog.behaviour import Behaviour
from b4cklog.placement import model as placement_model
from b4cklog.placement.label import (
    _soft_label,
    _strength_word,
    _subdivide,
    _subdivide_completionist,
    _subdivide_obsessive,
    place,
)

# Raw-axis synthetic centers, spread in the same qualitative pattern as the
# five archetypes — not the production z-score prototypes in model.py (those
# are relative to a real seed's distribution, not meaningful as raw synthetic
# generator centers; see model.py's comment on `_ARCHETYPE_PROTOTYPES`).
_CENTERS = {
    "Completionist": (1.0, 0.80, 0.80),
    "Dabbler": (3.5, 0.10, 0.40),
    "Obsessive": (0.3, 0.60, 0.05),
    "Enthusiast": (2.0, 0.40, 0.40),
    "Curator": (3.0, 0.40, 0.15),
}


def _behaviour(depth, completion, consistency, shape=None) -> Behaviour:
    return Behaviour(
        depth_breadth=depth,
        completion_drive=completion,
        commitment_consistency=consistency,
        shape=shape or {},
    )


def _synthetic_profiles(n_per_cluster: int = 40, noise: float = 0.06) -> list[dict]:
    rng = np.random.default_rng(0)
    profiles = []
    for center in _CENTERS.values():
        points = rng.normal(loc=center, scale=noise, size=(n_per_cluster, 3))
        for depth, completion, consistency in points:
            profiles.append(
                {
                    "depth_breadth": float(depth),
                    "completion_drive": float(np.clip(completion, 0.0, 1.0)),
                    "commitment_consistency": float(np.clip(consistency, 0.0, 1.0)),
                }
            )
    return profiles


@pytest.fixture(scope="module")
def fitted_model():
    return placement_model.fit(_synthetic_profiles(), component_range=range(2, 8))


def _two_cluster_profiles(name_a: str, name_b: str, n_per_cluster: int = 60, noise: float = 0.35) -> list[dict]:
    """Only two of the five archetype regions, noisier than the five-cluster
    fixture so the two genuinely overlap — real blending needs real overlap,
    which five tight, far-apart clusters don't have at any of their midpoints."""
    rng = np.random.default_rng(1)
    profiles = []
    for center in (_CENTERS[name_a], _CENTERS[name_b]):
        points = rng.normal(loc=center, scale=noise, size=(n_per_cluster, 3))
        for depth, completion, consistency in points:
            profiles.append(
                {
                    "depth_breadth": float(depth),
                    "completion_drive": float(np.clip(completion, 0.0, 1.0)),
                    "commitment_consistency": float(np.clip(consistency, 0.0, 1.0)),
                }
            )
    return profiles


@pytest.fixture(scope="module")
def two_cluster_model():
    # Forced to exactly two components — this model exists only to test the
    # continuous read between two neighbours, not component selection.
    # Completionist and Dabbler are opposite on completion_drive, the
    # cleanest possible pair to keep unambiguous under any standardization.
    profiles = _two_cluster_profiles("Completionist", "Dabbler")
    return placement_model.fit(profiles, component_range=range(2, 3))


# --- _strength_word / _soft_label: pure wording logic ---


def test_strength_word_bands():
    assert _strength_word(0.50) == "strong"
    assert _strength_word(0.30) == "moderate"
    assert _strength_word(0.15) == "a hint of"
    assert _strength_word(0.05) is None


def test_soft_label_with_no_secondary_states_primary_plainly():
    assert _soft_label("Completionist", None, None) == "You play most like a Completionist."


def test_soft_label_with_negligible_secondary_omits_it():
    label = _soft_label("Completionist", "Dabbler", 0.05)
    assert label == "You play most like a Completionist."


def test_soft_label_with_real_secondary_names_both():
    label = _soft_label("Completionist", "Dabbler", 0.40)
    assert label == "You play most like a Completionist, with strong Dabbler tendencies."


def test_soft_label_uses_an_before_a_vowel_sound():
    assert _soft_label("Enthusiast", None, None) == "You play most like an Enthusiast."
    assert _soft_label("Obsessive", None, None) == "You play most like an Obsessive."


def test_soft_label_for_an_unclassified_primary_admits_the_taxonomy_gap():
    label = _soft_label("Unclassified-2", "Dabbler", 0.40)
    assert "gap in the taxonomy" in label
    assert "Unclassified" not in label  # no raw internal name leaks to the reader


def test_soft_label_appends_the_outlier_caveat_when_flagged():
    plain = _soft_label("Completionist", None, None, outlier=False)
    flagged = _soft_label("Completionist", None, None, outlier=True)
    assert flagged.startswith(plain)
    assert "rough guess" in flagged


# --- subdivisions: fire on shape, admit when they can't ---


def test_completionist_subdivides_breadth_on_low_bimodality():
    sub = _subdivide_completionist({"games_played": 10, "bimodality": 0.20})
    assert sub.name == "Breadth Completionist"
    assert sub.confident


def test_completionist_subdivides_depth_on_high_bimodality():
    sub = _subdivide_completionist({"games_played": 10, "bimodality": 0.80})
    assert sub.name == "Depth Completionist"
    assert sub.confident


def test_completionist_subdivision_admits_too_few_games():
    sub = _subdivide_completionist({"games_played": 2, "bimodality": 0.0})
    assert sub.name == "undetermined"
    assert not sub.confident


def test_obsessive_subdivision_is_honestly_deferred_pending_phase_5():
    sub = _subdivide_obsessive({"games_played": 50, "bimodality": 0.0})
    assert sub.name == "undetermined"
    assert not sub.confident


@pytest.mark.parametrize("archetype", ["Dabbler", "Enthusiast", "Curator"])
def test_archetypes_without_a_subdivision_get_none(archetype):
    assert _subdivide(archetype, {"games_played": 10, "bimodality": 0.5}) is None


# --- place(): the continuous, deterministic end-to-end read ---


def test_place_is_deterministic(fitted_model):
    behaviour = _behaviour(1.8, 0.75, 0.75, shape={"games_played": 10, "bimodality": 0.2})
    first = place(fitted_model, behaviour)
    second = place(fitted_model, behaviour)
    assert first.soft_label == second.soft_label
    assert first.primary == second.primary
    assert first.responsibilities == second.responsibilities


def test_place_at_a_cluster_center_reads_confidently(two_cluster_model):
    depth, completion, consistency = _CENTERS["Completionist"]
    behaviour = _behaviour(depth, completion, consistency, shape={"games_played": 10, "bimodality": 0.2})
    placement = place(two_cluster_model, behaviour)
    assert placement.primary == "Completionist"
    assert placement.primary_probability > 0.85
    assert placement.subdivision is not None  # Completionist always subdivides
    assert not placement.outlier


def test_place_flags_a_true_outlier_and_says_so(two_cluster_model):
    # Nowhere near either cluster this model was fit on. predict_proba will
    # still hand back a confident-looking split between the two components —
    # this is the check that catches that and says so, since membership
    # probabilities alone can't (PlacementModel.outlier_log_likelihood).
    behaviour = _behaviour(50.0, 50.0, 50.0, shape={"games_played": 10, "bimodality": 0.2})
    placement = place(two_cluster_model, behaviour)
    assert placement.outlier
    assert "rough guess" in placement.soft_label


def test_place_between_two_clusters_is_blended_not_snapped(two_cluster_model):
    # Sitting exactly between the two centers this model was fit on, real
    # overlap between the components should split the read between both —
    # not snap to whichever one happens to be nominally nearest.
    completionist = np.array(_CENTERS["Completionist"])
    dabbler = np.array(_CENTERS["Dabbler"])
    midpoint = (completionist + dabbler) / 2
    behaviour = _behaviour(*midpoint, shape={"games_played": 10, "bimodality": 0.2})
    placement = place(two_cluster_model, behaviour)

    # A genuine blend: the top two archetypes are the two the midpoint sits
    # between, and neither dominates the way a cluster-center read does.
    top_two = {placement.primary, placement.secondary}
    assert top_two == {"Completionist", "Dabbler"}
    assert placement.primary_probability < 0.85
    assert placement.secondary_probability > 0.12
    assert placement.secondary in placement.soft_label
