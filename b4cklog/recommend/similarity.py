"""Player-similarity matching (Goal 6.2): what behaviourally similar seed
members actually spent real time in, weighted by how near they sit
(PROJECT.md, "How recommendations are generated"). This engine "only means
anything once enough similar players exist" — it's the sharpener, not the
load-bearing wall (PROJECT.md, "A note on scale"); `blend.py` is what caps how
much it's trusted.

Pure logic throughout, like `placement/label.py`: no store access here. The
caller looks up seed profiles and playtimes and passes them in, so this module
stays testable against synthetic data and agnostic to how the reference store
is wired.
"""

from dataclasses import dataclass

import numpy as np

from b4cklog.placement import AXES, Placement, PlacementModel

# Distance-weight kernel bandwidth, in the same standardized units the mixture
# was fit on (one seed standard deviation). A neighbor at this distance still
# carries meaningful weight; one several bandwidths out decays toward zero —
# the "weighted by how near they sit" PROJECT.md asks for.
_KERNEL_BANDWIDTH = 1.0

# How many of the nearest seed members to consider at all. Bounds request-time
# cost; beyond this a neighbor's kernel weight is negligible at seed scale
# anyway.
DEFAULT_K = 40

# A neighbor's kernel weight below this is noise, not signal — dropped so it
# doesn't dilute the "how many neighbors really back this" density read.
_MIN_NEIGHBOR_WEIGHT = 0.05

# Total neighbor weight at which the density read is already "densely
# populated" and further neighbors stop pushing it higher. PROJECT.md says
# game-property matching "should" still carry the real weight even where the
# seed is crowded, so density saturates instead of climbing unbounded.
_DENSITY_SATURATION = 4.0


@dataclass(frozen=True)
class Neighbor:
    steam_id: str
    distance: float
    weight: float


def nearest_neighbors(
    model: PlacementModel, placement: Placement, seed_profiles: list[dict], *, k: int = DEFAULT_K
) -> list[Neighbor]:
    """The visitor's k nearest seed members in the same standardized space the
    mixture was fit on, each carrying a Gaussian-kernel distance weight.
    """
    if not seed_profiles:
        return []
    raw = np.array([[p[axis] for axis in AXES] for p in seed_profiles])
    standardized = model.scaler.transform(raw)
    point = np.array(placement.coordinates)
    distances = np.linalg.norm(standardized - point, axis=1)

    order = np.argsort(distances)[:k]
    neighbors = []
    for i in order:
        distance = float(distances[i])
        weight = float(np.exp(-0.5 * (distance / _KERNEL_BANDWIDTH) ** 2))
        if weight < _MIN_NEIGHBOR_WEIGHT:
            continue
        neighbors.append(Neighbor(steam_id=seed_profiles[i]["steam_id"], distance=distance, weight=weight))
    return neighbors


def neighborhood_density(neighbors: list[Neighbor]) -> float:
    """How much real company the visitor has nearby, in [0, 1). Sums neighbor
    weight (each neighbor contributes at most 1.0, at distance 0) and
    saturates it via `_DENSITY_SATURATION` — see that constant for why.
    """
    total = sum(n.weight for n in neighbors)
    return float(1.0 - np.exp(-total / _DENSITY_SATURATION))


@dataclass(frozen=True)
class SimilarityFit:
    score: float          # 0..1, normalized within the candidate set scored alongside it
    weighted_hours: float  # raw signal: neighbor-weighted mean hours among engaged neighbors, 0 if none
    engaged_neighbors: int  # neighbors with any logged playtime in this game — the read's evidence count


def _engaged_hours(
    neighbors: list[Neighbor], playtimes_by_steam_id: dict[str, dict[int, int]], app_id: int
) -> tuple[float, int]:
    """Weighted mean hours among neighbors who actually have playtime logged
    for this game. A neighbor with none simply doesn't vote — "haven't played
    it" is real information, already captured by how few neighbors remain
    engaged, not smuggled in as a zero that would flatten every candidate
    toward "not much."
    """
    weight_sum = 0.0
    hour_weight_sum = 0.0
    engaged = 0
    for neighbor in neighbors:
        minutes = playtimes_by_steam_id.get(neighbor.steam_id, {}).get(app_id)
        if not minutes:
            continue
        engaged += 1
        weight_sum += neighbor.weight
        hour_weight_sum += neighbor.weight * (minutes / 60.0)
    if weight_sum == 0.0:
        return 0.0, 0
    return hour_weight_sum / weight_sum, engaged


def score_similarity(
    neighbors: list[Neighbor],
    playtimes_by_steam_id: dict[str, dict[int, int]],
    app_ids: list[int],
) -> dict[int, SimilarityFit]:
    """Score every candidate at once — normalization is relative to the
    candidate set being ranked together (min-max on weighted hours), not to
    some absolute hour count that would make an obscure pool incomparable to
    a popular one.
    """
    raw = {app_id: _engaged_hours(neighbors, playtimes_by_steam_id, app_id) for app_id in app_ids}
    max_hours = max((hours for hours, _ in raw.values()), default=0.0)

    fits = {}
    for app_id, (hours, engaged) in raw.items():
        score = hours / max_hours if max_hours > 0 else 0.0
        fits[app_id] = SimilarityFit(score=score, weighted_hours=hours, engaged_neighbors=engaged)
    return fits
