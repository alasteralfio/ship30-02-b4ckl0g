"""The placement visual (Goal 7.2): well-formed SVG, one spoke per named
archetype (never the raw coordinates), the primary archetype reads as
distinct, and an outlier placement visibly says so."""

from b4cklog.placement import Placement
from b4cklog.web.svg import placement_svg


def _placement(responsibilities: dict[str, float], *, primary=None, outlier: bool = False) -> Placement:
    ranked = sorted(responsibilities.items(), key=lambda kv: -kv[1])
    primary = primary or ranked[0][0]
    return Placement(
        coordinates=(0.0, 0.0, 0.0),
        responsibilities=responsibilities,
        primary=primary,
        primary_probability=responsibilities[primary],
        secondary=None,
        secondary_probability=None,
        outlier=outlier,
        soft_label="",
        subdivision=None,
    )


def test_svg_is_well_formed():
    svg = placement_svg(_placement({"Completionist": 0.6, "Dabbler": 0.4}))
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert svg.count("<svg") == 1


def test_svg_labels_every_named_archetype():
    svg = placement_svg(_placement({"Completionist": 0.6, "Dabbler": 0.4}))
    for name in ("Completionist", "Dabbler", "Obsessive", "Enthusiast", "Curator"):
        assert name in svg


def test_svg_never_prints_raw_coordinates():
    placement = Placement(
        coordinates=(1.2345, -6.789, 0.001),
        responsibilities={"Enthusiast": 1.0},
        primary="Enthusiast",
        primary_probability=1.0,
        secondary=None,
        secondary_probability=None,
        outlier=False,
        soft_label="",
        subdivision=None,
    )
    svg = placement_svg(placement)
    assert "1.2345" not in svg
    assert "-6.789" not in svg


def test_svg_omits_unclassified_components_as_spokes():
    svg = placement_svg(_placement({"Unclassified-0": 1.0}, primary="Unclassified-0"))
    assert "Unclassified" not in svg


def test_svg_marks_the_primary_archetype_distinctly():
    svg = placement_svg(_placement({"Curator": 0.7, "Enthusiast": 0.3}, primary="Curator"))
    assert 'font-weight="600"' in svg


def test_svg_dashes_the_shape_when_placement_is_an_outlier():
    plain = placement_svg(_placement({"Completionist": 1.0}, outlier=False))
    flagged = placement_svg(_placement({"Completionist": 1.0}, outlier=True))
    assert "stroke-dasharray" not in plain
    assert "stroke-dasharray" in flagged
