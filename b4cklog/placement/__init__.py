"""Standardize against the seed, place via the frozen mixture, soft-label."""

from .label import Placement, Subdivision, place
from .model import AXES, ARTIFACT_PATH, UNCLASSIFIED_PREFIX, PlacementModel, fit, load, save

__all__ = [
    "AXES",
    "ARTIFACT_PATH",
    "UNCLASSIFIED_PREFIX",
    "PlacementModel",
    "fit",
    "load",
    "save",
    "Placement",
    "Subdivision",
    "place",
]
