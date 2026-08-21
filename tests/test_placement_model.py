"""Fitting and freezing the placement model (Goal 4.1): BIC picks a sensible
component count on well-separated data, every archetype gets a nearest
component, the fit is deterministic, and the frozen artifact round-trips
through save/load with identical predictions. Synthetic data throughout —
no seed crawl or live key needed."""

import numpy as np
import pytest

from b4cklog.placement import model as placement_model

# Five well-separated raw-axis centers, spread in the same qualitative pattern
# as the five archetypes (wide vs. narrow breadth, high vs. low completion and
# consistency) but *not* the production prototypes themselves — those are
# z-scores against the seed's own distribution (see model.py's comment on
# `_ARCHETYPE_PROTOTYPES`), and this fixture's job is only to hand `fit()` five
# obviously distinct clusters, however this synthetic dataset happens to
# standardize.
_RAW_CLUSTER_CENTERS = {
    "Completionist": (1.0, 0.80, 0.80),   # low-ish breadth, high completion, high consistency
    "Dabbler": (3.5, 0.10, 0.40),         # wide breadth, low completion
    "Obsessive": (0.3, 0.60, 0.05),       # very narrow breadth, lopsided consistency
    "Enthusiast": (2.0, 0.40, 0.40),      # the balanced middle
    "Curator": (3.0, 0.40, 0.15),         # wide breadth, lopsided consistency
}


def _synthetic_profiles(n_per_cluster: int = 40, noise: float = 0.06) -> list[dict]:
    """Five tight, well-separated clusters — exactly the shape BIC should have
    no trouble resolving to five components."""
    rng = np.random.default_rng(0)
    profiles = []
    for center in _RAW_CLUSTER_CENTERS.values():
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


@pytest.fixture
def synthetic_profiles() -> list[dict]:
    return _synthetic_profiles()


def test_fit_is_deterministic(synthetic_profiles):
    first = placement_model.fit(synthetic_profiles, component_range=range(2, 7))
    second = placement_model.fit(synthetic_profiles, component_range=range(2, 7))
    assert first.mixture.n_components == second.mixture.n_components
    np.testing.assert_allclose(first.mixture.means_, second.mixture.means_)


def test_bic_prefers_five_on_five_well_separated_clusters(synthetic_profiles):
    fitted = placement_model.fit(synthetic_profiles, component_range=range(2, 8))
    assert fitted.mixture.n_components == 5
    assert set(fitted.bic_by_component_count) == set(range(2, 8))


def test_every_archetype_gets_a_nearest_component(synthetic_profiles):
    fitted = placement_model.fit(synthetic_profiles, component_range=range(2, 8))
    assert set(fitted.archetype_by_component.values()) == set(_RAW_CLUSTER_CENTERS)


def test_component_count_can_diverge_from_five():
    # A single blob has no real structure to split into five — BIC should
    # prefer far fewer components than the working archetype count, which is
    # exactly the emergent-taxonomy signal the roadmap asks this to record.
    rng = np.random.default_rng(1)
    points = rng.normal(loc=(2.0, 0.5, 0.5), scale=0.05, size=(120, 3))
    profiles = [
        {"depth_breadth": float(d), "completion_drive": float(c), "commitment_consistency": float(k)}
        for d, c, k in points
    ]
    fitted = placement_model.fit(profiles, component_range=range(2, 8))
    assert fitted.mixture.n_components < 5


def test_save_and_load_round_trip_predicts_identically(tmp_path, synthetic_profiles):
    fitted = placement_model.fit(synthetic_profiles, component_range=range(2, 6))
    path = tmp_path / "placement_model.pkl"
    placement_model.save(fitted, path)
    loaded = placement_model.load(path)

    sample = placement_model._matrix(synthetic_profiles[:10])
    original_probs = fitted.mixture.predict_proba(fitted.scaler.transform(sample))
    loaded_probs = loaded.mixture.predict_proba(loaded.scaler.transform(sample))
    np.testing.assert_allclose(original_probs, loaded_probs)
    assert loaded.archetype_by_component == fitted.archetype_by_component
    assert loaded.outlier_log_likelihood == fitted.outlier_log_likelihood


def test_outlier_threshold_separates_seed_members_from_a_true_outlier(synthetic_profiles):
    # `predict_proba` alone can't tell a well-supported point from one no
    # component actually explains — that's what this threshold is for
    # (PlacementModel.outlier_log_likelihood).
    fitted = placement_model.fit(synthetic_profiles, component_range=range(2, 8))
    typical = placement_model._matrix(synthetic_profiles[:1])
    typical_ll = fitted.mixture.score_samples(fitted.scaler.transform(typical))[0]
    assert typical_ll >= fitted.outlier_log_likelihood

    far_outlier = np.array([[50.0, 50.0, 50.0]])
    outlier_ll = fitted.mixture.score_samples(fitted.scaler.transform(far_outlier))[0]
    assert outlier_ll < fitted.outlier_log_likelihood


def test_load_missing_artifact_raises_with_the_fit_command(tmp_path):
    with pytest.raises(FileNotFoundError, match="pipeline.fit_placement"):
        placement_model.load(tmp_path / "does_not_exist.pkl")


class _FakeMixture:
    """A stand-in with just the attribute `_label_components` reads, so the
    naming logic is tested directly against exact prototype coordinates
    instead of through a fit that could land near-but-not-exactly on them."""

    def __init__(self, means):
        self.means_ = np.array(means)


def test_label_components_recovers_each_prototype_exactly():
    names = list(placement_model._ARCHETYPE_PROTOTYPES)
    means = [placement_model._ARCHETYPE_PROTOTYPES[name] for name in names]
    labels = placement_model._label_components(_FakeMixture(means))
    assert labels == dict(enumerate(names))


def test_label_components_marks_a_far_outlier_as_unclassified():
    # Nowhere near any of the five prototypes — this should read as a gap in
    # the taxonomy, not get coerced onto whichever name is least-far.
    labels = placement_model._label_components(_FakeMixture([(5.0, 5.0, 5.0)]))
    assert labels[0].startswith(placement_model.UNCLASSIFIED_PREFIX)


def test_label_components_picks_nearest_under_perturbation():
    # A mean nudged slightly off a prototype should still resolve to that
    # prototype, not drift to a neighbour — the naming should be robust to a
    # real fitted component not landing exactly on the hand-picked point.
    completionist = np.array(placement_model._ARCHETYPE_PROTOTYPES["Completionist"])
    nudged = completionist + 0.05
    labels = placement_model._label_components(_FakeMixture([nudged]))
    assert labels[0] == "Completionist"


def test_fit_rejects_too_few_profiles_for_any_candidate_count():
    profiles = [
        {"depth_breadth": 1.0, "completion_drive": 0.5, "commitment_consistency": 0.5}
    ]
    with pytest.raises(ValueError):
        placement_model.fit(profiles, component_range=range(2, 4))
