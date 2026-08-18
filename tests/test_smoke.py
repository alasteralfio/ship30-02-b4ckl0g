"""Smoke test: the package and its subpackages import and stand up."""

import importlib


def test_package_version():
    import b4cklog

    assert b4cklog.__version__ == "0.1.0"


def test_subpackages_import():
    for name in (
        "b4cklog.steam",
        "b4cklog.behaviour",
        "b4cklog.placement",
        "b4cklog.recommend",
        "b4cklog.web",
        "pipeline",
    ):
        assert importlib.import_module(name) is not None
