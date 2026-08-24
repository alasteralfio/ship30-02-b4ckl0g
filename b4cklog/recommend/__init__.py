"""Game-property and player-similarity scoring, blended by proximity."""

from .blend import MAX_SIMILARITY_WEIGHT, Recommendation, RecommendationSet, recommend
from .properties import PropertyFit, score_game_properties
from .similarity import (
    DEFAULT_K,
    Neighbor,
    SimilarityFit,
    nearest_neighbors,
    neighborhood_density,
    score_similarity,
)

__all__ = [
    "MAX_SIMILARITY_WEIGHT",
    "Recommendation",
    "RecommendationSet",
    "recommend",
    "PropertyFit",
    "score_game_properties",
    "DEFAULT_K",
    "Neighbor",
    "SimilarityFit",
    "nearest_neighbors",
    "neighborhood_density",
    "score_similarity",
]
