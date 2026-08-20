"""The seed diversity summary — the "did we bias the seed" gate (Checkpoint 5).

Reads the crawled reference crowd and shows how it spreads over the three axes:
per-axis histograms, and a coverage count over heuristic archetype regions so a
starved region is visible. This is the check PROJECT.md calls the most important
human one in the project — bias the seed and every cluster downstream inherits
it — so the summary is built to make a clump obvious, not to reassure.

Two honesty notes baked in:

- The regions here are *heuristic* thresholds on the axes, not the placement
  model. The Gaussian mixture (Phase 4) draws the real boundaries; this is a
  coarse coverage read to catch a lopsided seed before we ever fit it.
- Profiles that match no region are counted and surfaced, not hidden. A player
  who fits no archetype is the emergent-taxonomy signal, not an error — a large
  "fits no region" share means the taxonomy is incomplete, which is exactly the
  kind of thing this summary exists to show.

Run:  python -m pipeline.seed_summary
"""

import sys
from collections.abc import Callable
from statistics import mean, pstdev

from b4cklog import store

# Heuristic cut points on each axis, used only for the coverage read below. The
# axis semantics are fixed in behaviour/reduce.py: depth_breadth is ln(effective
# games played) so ~1.6 is about 5 games and ~3.0 about 20; the other two are
# 0..1. These are deliberately coarse — the model, not these lines, decides types.
_LOW_BREADTH, _HIGH_BREADTH = 1.6, 3.0
_LOW_COMPLETION, _HIGH_COMPLETION = 0.30, 0.60
_LOW_CONSISTENCY, _HIGH_CONSISTENCY = 0.35, 0.60
_HIGH_BIMODALITY = 0.60

_AXES = ("depth_breadth", "completion_drive", "commitment_consistency")


def _regions() -> dict[str, Callable[[dict], bool]]:
    """Archetype regions as predicates on a profile, from PROJECT.md's definitions.

    A profile can match several or none — this is coverage, not classification.
    """
    def bimodality(p) -> float:
        return p["shape_features"].get("bimodality", 0.0)

    return {
        # High completion, played evenly — finishes what it starts.
        "Completionist": lambda p: p["completion_drive"] >= _HIGH_COMPLETION
        and p["commitment_consistency"] >= _HIGH_CONSISTENCY,
        # Wide library, low completion — tries a lot, commits to little.
        "Dabbler": lambda p: p["depth_breadth"] >= _HIGH_BREADTH
        and p["completion_drive"] < _LOW_COMPLETION,
        # Few effective games and lopsided time — one game dwarfs the rest.
        "Obsessive": lambda p: p["depth_breadth"] < _LOW_BREADTH
        and p["commitment_consistency"] < _LOW_CONSISTENCY,
        # Split-shaped library (many short, a few very long) with real breadth.
        "Curator": lambda p: bimodality(p) >= _HIGH_BIMODALITY
        and p["depth_breadth"] >= _LOW_BREADTH,
        # The balanced middle — a bit of everything, nothing extreme.
        "Enthusiast": lambda p: _LOW_COMPLETION <= p["completion_drive"] < _HIGH_COMPLETION
        and _LOW_BREADTH <= p["depth_breadth"] < _HIGH_BREADTH
        and p["commitment_consistency"] >= _LOW_CONSISTENCY,
    }


def _axis_stats(values: list[float]) -> dict:
    return {
        "min": min(values),
        "max": max(values),
        "mean": mean(values),
        "std": pstdev(values) if len(values) > 1 else 0.0,
    }


def _histogram(values: list[float], lo: float, hi: float, bins: int = 10) -> list[int]:
    """Count values into `bins` equal buckets over [lo, hi]. The top edge is
    inclusive so the maximum value lands in the last bucket, not off the end."""
    counts = [0] * bins
    span = hi - lo
    for v in values:
        if span == 0:
            counts[0] += 1
            continue
        idx = min(bins - 1, int((v - lo) / span * bins))
        counts[idx] += 1
    return counts


def summarize(profiles: list[dict]) -> dict:
    """Turn crawled profiles into a diversity report: per-axis spread, region
    coverage, and the share fitting no named region."""
    total = len(profiles)
    if total == 0:
        return {"total": 0, "axes": {}, "coverage": {}, "unmatched": 0}
    axes = {}
    for axis in _AXES:
        values = [p[axis] for p in profiles]
        lo = 0.0
        hi = max(values) if axis == "depth_breadth" else 1.0
        axes[axis] = {"stats": _axis_stats(values), "histogram": _histogram(values, lo, hi), "hi": hi}

    regions = _regions()
    coverage = {name: sum(1 for p in profiles if pred(p)) for name, pred in regions.items()}
    unmatched = sum(1 for p in profiles if not any(pred(p) for pred in regions.values()))

    return {"total": total, "axes": axes, "coverage": coverage, "unmatched": unmatched}


def _bar(count: int, total: int, width: int = 30) -> str:
    filled = 0 if total == 0 else round(count / total * width)
    return "█" * filled + "·" * (width - filled)


def render(summary: dict) -> str:
    total = summary["total"]
    if total == 0:
        return "The seed is empty. Run `python -m pipeline.crawl_seed` first."

    lines = [f"Seed diversity over {total} profiles", "=" * 40, ""]

    for axis in _AXES:
        info = summary["axes"][axis]
        s = info["stats"]
        lines.append(
            f"{axis}   mean {s['mean']:.2f}  std {s['std']:.2f}  "
            f"range [{s['min']:.2f}, {s['max']:.2f}]"
        )
        hist, hi = info["histogram"], info["hi"]
        peak = max(hist) or 1
        for i, count in enumerate(hist):
            edge_lo = hi * i / len(hist)
            edge_hi = hi * (i + 1) / len(hist)
            bar = "▮" * round(count / peak * 24)
            lines.append(f"  {edge_lo:5.2f}–{edge_hi:5.2f} {bar} {count}")
        lines.append("")

    lines.append("Archetype-region coverage (heuristic, pre-model):")
    thin = max(1, round(total * 0.05))
    for name, count in summary["coverage"].items():
        flag = "  << thin" if count < thin else ""
        lines.append(f"  {count:>4}  {_bar(count, total)}  {name}{flag}")
    unmatched = summary["unmatched"]
    lines.append(
        f"\n  {unmatched:>4}  {_bar(unmatched, total)}  fits no region "
        f"({unmatched / total:.0%} — the emergent-taxonomy signal, not an error)"
    )
    return "\n".join(lines)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    conn = store.connect()
    try:
        profiles = store.all_seed_profiles(conn)
    finally:
        conn.close()
    print(render(summarize(profiles)))


if __name__ == "__main__":
    main()
