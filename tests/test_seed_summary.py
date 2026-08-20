"""The diversity summary: regions count the right profiles, the emergent-taxonomy
'fits no region' share is surfaced, and histograms bin correctly. Pure — no I/O."""

from pipeline.seed_summary import render, summarize


def _profile(depth, completion, consistency, bimodality=0.0):
    return {
        "depth_breadth": depth,
        "completion_drive": completion,
        "commitment_consistency": consistency,
        "shape_features": {"bimodality": bimodality},
    }


# One clean representative per region, plus one that fits none.
_COMPLETIONIST = _profile(2.0, 0.80, 0.70)
_DABBLER = _profile(3.5, 0.10, 0.50)
_OBSESSIVE = _profile(1.0, 0.90, 0.20)
_ENTHUSIAST = _profile(2.0, 0.45, 0.50)
_CURATOR = _profile(2.5, 0.45, 0.30, bimodality=0.70)
_UNMATCHED = _profile(2.0, 0.20, 0.50)  # mid-everything but too low to complete


def test_each_region_counts_its_own_representative():
    summary = summarize([_COMPLETIONIST, _DABBLER, _OBSESSIVE, _ENTHUSIAST, _CURATOR])
    coverage = summary["coverage"]
    assert coverage["Completionist"] == 1
    assert coverage["Dabbler"] == 1
    assert coverage["Obsessive"] == 1
    assert coverage["Enthusiast"] == 1
    assert coverage["Curator"] == 1


def test_unmatched_profile_is_counted_as_the_taxonomy_signal():
    summary = summarize([_COMPLETIONIST, _UNMATCHED])
    assert summary["unmatched"] == 1


def test_total_and_axis_stats():
    summary = summarize([_COMPLETIONIST, _DABBLER, _OBSESSIVE])
    assert summary["total"] == 3
    breadth = summary["axes"]["depth_breadth"]["stats"]
    assert breadth["min"] == 1.0
    assert breadth["max"] == 3.5


def test_histogram_places_values_in_the_right_bucket():
    # completion_drive spans [0, 1] in 10 bins: 0.05 -> bin 0, 0.95 -> bin 9.
    profiles = [_profile(2.0, 0.05, 0.5), _profile(2.0, 0.95, 0.5)]
    hist = summarize(profiles)["axes"]["completion_drive"]["histogram"]
    assert hist[0] == 1
    assert hist[9] == 1
    assert sum(hist) == 2


def test_render_handles_empty_seed():
    assert "empty" in render(summarize([])).lower()
