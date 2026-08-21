"""Fit and freeze the placement model (Goal 4.1).

A `StandardScaler` and a `GaussianMixture` fit once on the seed's three axes,
chosen and frozen here, then loaded read-only everywhere else. The model is
never refit at request time — that's what keeps a returning Steam ID from
getting a jarringly different label because the space moved under it
(PROJECT.md, "Placement").

Component count is chosen by BIC, weighed against the five working archetypes
rather than forced to them: if the data's real minimum sits elsewhere, that's
the emergent-taxonomy signal PROJECT.md asks to record, not paper over.
"""

import math
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

AXES = ("depth_breadth", "completion_drive", "commitment_consistency")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ARTIFACT_PATH = _PROJECT_ROOT / "data" / "placement_model.pkl"

# The working archetype set (PROJECT.md). BIC picks the component count against
# this range; five is the prior, not a floor or a ceiling.
DEFAULT_COMPONENT_RANGE = range(2, 9)

# Rough centers for each archetype, used only to name a fitted component by
# which prototype its mean sits nearest to. Given in *standardized* units —
# z-scores relative to the seed's own mean and spread — deliberately, not raw
# axis values: PROJECT.md is explicit that a z-score only means something
# against the seed as reference, and raw-unit guesses learned that the hard
# way here. An early version hardcoded "high completion" as a raw 0.75, which
# looked reasonable in isolation but sat 2.4 std above this seed's actual mean
# (0.32) — every prototype ended up in the same extreme corner of the space and
# every component collapsed onto whichever one was least extreme. These
# magnitudes (roughly ±1 std) describe *direction and rough distance from the
# crowd's center*, which is what PROJECT.md's archetype descriptions actually
# claim ("high completion drive", "very low breadth") — not an absolute claim
# about where real players sit. The model's own component means are the real
# output; this only assigns them a name.
_ARCHETYPE_PROTOTYPES = {
    "Completionist": (-0.3, 1.0, 1.0),   # low-mid breadth, high completion, high consistency
    "Dabbler": (1.2, -1.0, 0.0),         # wide breadth, low completion, unremarkable consistency
    "Obsessive": (-1.2, 0.4, -1.2),      # very narrow breadth, lopsided (low) consistency
    "Enthusiast": (0.0, 0.0, 0.0),       # the balanced middle, by construction
    "Curator": (0.9, 0.0, -0.6),         # wide breadth, split/lopsided consistency
}

# Beyond this distance (in the same standardized units as the prototypes
# above), the nearest prototype isn't a real match — it's just the least-bad
# option among five that all sit far away. Forcing a name on it would hide
# exactly the "doesn't fit any archetype" signal PROJECT.md treats as real
# data (Emergent Taxonomy), not an error to smooth over. Chosen with headroom
# over the prototypes' own spacing: the closest pair of working archetypes
# (Enthusiast–Curator) sit 1.08 apart, so a genuine match still resolves to a
# name; only a component with no real match sits past this line.
UNCLASSIFIED_PREFIX = "Unclassified"
_UNMAPPED_DISTANCE_THRESHOLD = 1.75


@dataclass(frozen=True)
class PlacementModel:
    """The frozen artifact: everything `place()` needs and nothing it computes."""

    scaler: StandardScaler
    mixture: GaussianMixture
    # Component index -> archetype name. Names can repeat (BIC found more than
    # one natural cluster inside a working archetype) or go unused (that
    # archetype's prototype region is thin or absent in this seed) — both are
    # readable straight off this dict, not hidden by it.
    archetype_by_component: dict[int, str]
    bic_by_component_count: dict[int, float]
    # 5th percentile of the seed's own log-likelihood under the fitted mixture
    # (`mixture.score_samples`). `predict_proba` only compares a point against
    # the components that exist — it has no notion of "nobody here looks like
    # you," so a player who sits nowhere any real seed member sits can still
    # get a confident-looking 100% for whichever component decays slowest in
    # that direction. This threshold is what `place()` checks instead: a point
    # scoring below where 95% of the seed itself scores is in territory the
    # model has little to no evidence about, and that has to be said outright
    # (PROJECT.md, "Honesty over comfort") rather than dressed up as a clean read.
    outlier_log_likelihood: float
    seed_size: int
    fitted_at: str


def _matrix(profiles: list[dict]) -> np.ndarray:
    return np.array([[p[axis] for axis in AXES] for p in profiles], dtype=float)


def _choose_mixture(
    X: np.ndarray, component_range: range, n_init: int, random_state: int
) -> tuple[GaussianMixture, dict[int, float]]:
    """Fit one mixture per candidate component count, return the lowest-BIC fit
    plus every score — the full curve is what makes a non-five answer legible."""
    scores: dict[int, float] = {}
    best_gmm, best_bic = None, math.inf
    for n in component_range:
        if n > len(X):
            break
        gmm = GaussianMixture(
            n_components=n, covariance_type="full", n_init=n_init, random_state=random_state
        ).fit(X)
        bic = gmm.bic(X)
        scores[n] = bic
        if bic < best_bic:
            best_gmm, best_bic = gmm, bic
    if best_gmm is None:
        raise ValueError(f"Not enough profiles ({len(X)}) to fit any candidate component count.")
    return best_gmm, scores


def _label_components(mixture: GaussianMixture) -> dict[int, str]:
    """Name each fitted component by the nearest archetype prototype, both
    read in the same standardized space the mixture was fit on.

    A component too far from every prototype is labelled
    `Unclassified-{i}` instead of coerced onto whichever name happens to be
    least-far — see `_UNMAPPED_DISTANCE_THRESHOLD`. The index keeps two
    distinct unclassified components from being silently merged into one.
    """
    names = list(_ARCHETYPE_PROTOTYPES)
    prototypes = np.array([_ARCHETYPE_PROTOTYPES[n] for n in names])
    labels = {}
    for i, mean in enumerate(mixture.means_):
        distances = np.linalg.norm(prototypes - mean, axis=1)
        nearest = int(np.argmin(distances))
        if distances[nearest] > _UNMAPPED_DISTANCE_THRESHOLD:
            labels[i] = f"{UNCLASSIFIED_PREFIX}-{i}"
        else:
            labels[i] = names[nearest]
    return labels


def fit(
    profiles: list[dict],
    *,
    component_range: range = DEFAULT_COMPONENT_RANGE,
    n_init: int = 5,
    random_state: int = 0,
) -> PlacementModel:
    """Fit the standardizer and mixture on a set of seed profiles (dicts with
    the three axis keys, as `store.all_seed_profiles` returns)."""
    X_raw = _matrix(profiles)
    scaler = StandardScaler().fit(X_raw)
    X = scaler.transform(X_raw)
    mixture, bic_by_count = _choose_mixture(X, component_range, n_init, random_state)
    return PlacementModel(
        scaler=scaler,
        mixture=mixture,
        archetype_by_component=_label_components(mixture),
        bic_by_component_count=bic_by_count,
        outlier_log_likelihood=float(np.percentile(mixture.score_samples(X), 5)),
        seed_size=len(profiles),
        fitted_at=datetime.now(timezone.utc).isoformat(),
    )


def save(model: PlacementModel, path: Path = ARTIFACT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(model, f)


def load(path: Path = ARTIFACT_PATH) -> PlacementModel:
    if not path.exists():
        raise FileNotFoundError(
            f"No placement model at {path}. Run `python -m pipeline.fit_placement` first."
        )
    with path.open("rb") as f:
        return pickle.load(f)
