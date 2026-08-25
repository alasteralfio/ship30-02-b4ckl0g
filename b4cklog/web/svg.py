"""The placement visual (Goal 7.2): one inline-SVG picture of where a player
sits relative to the five archetypes.

Deliberately a radar chart over `placement.responsibilities`, not a plot of
`placement.coordinates`. PROJECT.md is explicit that the raw standardized
coordinates shouldn't be exposed ("the picture gives a feel for placement
without asking anyone to read a set of axes") — a spoke per archetype, its
length the player's actual soft-membership share, says "how much Curator, how
much Dabbler" without asking anyone to read a z-score. An Unclassified
component (PROJECT.md, "Emergent Taxonomy") simply isn't one of the five
spokes, so a player who reads as mostly unclassified draws a small, honestly
uninformative shape — the visual signal matches the truth rather than
forcing a shape onto data that doesn't have one.

Plain SVG, generated straight from Python — no charting library, matching
CLAUDE.md's "no JS framework, no build step."
"""

import math

from b4cklog.placement import Placement

# Fixed order and starting angle (top, clockwise) so the same archetype
# always lands in the same position on the page — a returning visitor's shape
# is comparable across lookups.
_ARCHETYPES = ("Completionist", "Dabbler", "Obsessive", "Enthusiast", "Curator")

_SIZE = 320
_CENTER = _SIZE / 2
_RADIUS = 110
_LABEL_OFFSET = 26

_AXIS_COLOR = "#b7bdc6"
_RING_COLOR = "#e2e5ea"
_SHAPE_COLOR = "#2f6feb"
_LABEL_COLOR = "#33383f"
_PRIMARY_COLOR = "#14233f"


def _point(angle: float, radius: float) -> tuple[float, float]:
    return (_CENTER + radius * math.cos(angle), _CENTER + radius * math.sin(angle))


def _angle_for(index: int) -> float:
    # -90 degrees so the first archetype sits at the top; clockwise from there.
    return -math.pi / 2 + index * (2 * math.pi / len(_ARCHETYPES))


def _text_anchor(angle: float) -> str:
    x = math.cos(angle)
    if x > 0.3:
        return "start"
    if x < -0.3:
        return "end"
    return "middle"


def placement_svg(placement: Placement) -> str:
    """A self-contained `<svg>...</svg>` string for one placement. Safe to
    embed directly in a template (`{{ svg | safe }}`)."""
    parts: list[str] = [
        f'<svg viewBox="0 0 {_SIZE} {_SIZE}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="Placement relative to the five archetypes">'
    ]

    # Reference rings at 25/50/75/100% so a spoke's length has a scale to
    # read against, without printing a single raw number.
    for fraction in (0.25, 0.5, 0.75, 1.0):
        ring = " ".join(f"{x:.1f},{y:.1f}" for x, y in (
            _point(_angle_for(i), _RADIUS * fraction) for i in range(len(_ARCHETYPES))
        ))
        parts.append(
            f'<polygon points="{ring}" fill="none" stroke="{_RING_COLOR}" stroke-width="1"/>'
        )

    # Axis spokes and labels.
    for i, name in enumerate(_ARCHETYPES):
        angle = _angle_for(i)
        x, y = _point(angle, _RADIUS)
        parts.append(
            f'<line x1="{_CENTER:.1f}" y1="{_CENTER:.1f}" x2="{x:.1f}" y2="{y:.1f}" '
            f'stroke="{_AXIS_COLOR}" stroke-width="1"/>'
        )
        lx, ly = _point(angle, _RADIUS + _LABEL_OFFSET)
        share = placement.responsibilities.get(name, 0.0)
        color = _PRIMARY_COLOR if name == placement.primary else _LABEL_COLOR
        weight = "600" if name == placement.primary else "400"
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{_text_anchor(angle)}" '
            f'font-family="sans-serif" font-size="12" font-weight="{weight}" fill="{color}">'
            f"{name} {share:.0%}</text>"
        )

    # The player's shape: one vertex per archetype at its responsibility share.
    shape_points = [
        _point(_angle_for(i), _RADIUS * placement.responsibilities.get(name, 0.0))
        for i, name in enumerate(_ARCHETYPES)
    ]
    points_attr = " ".join(f"{x:.1f},{y:.1f}" for x, y in shape_points)
    # A dashed outline for an outlier placement — the shape itself is still
    # the model's real relative read, but the dashing signals the absolute
    # fit is weak (PROJECT.md, "Honesty over comfort"), matching the soft
    # label's own outlier caveat rather than presenting a clean-looking shape.
    dash = ' stroke-dasharray="6,4"' if placement.outlier else ""
    parts.append(
        f'<polygon points="{points_attr}" fill="{_SHAPE_COLOR}" fill-opacity="0.25" '
        f'stroke="{_SHAPE_COLOR}" stroke-width="2"{dash}/>'
    )
    for x, y in shape_points:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{_SHAPE_COLOR}"/>')

    parts.append("</svg>")
    return "".join(parts)
