"""Smoke tests for the public API surface.

These tests exercise the import graph and the public `__all__` lists.
They do not connect to Resolve.
"""

from __future__ import annotations


def test_top_level_exports() -> None:
    import dvr

    expected = {
        "App",
        "Clip",
        "ClipQuery",
        "Project",
        "ProjectNamespace",
        "RenderJob",
        "RenderNamespace",
        "Resolve",
        "Timeline",
        "TimelineNamespace",
        "Track",
        "__version__",
        "errors",
    }
    assert expected.issubset(set(dvr.__all__))
    for name in expected:
        assert hasattr(dvr, name), f"dvr is missing public export {name!r}"


def test_cli_app_importable() -> None:
    """The CLI Typer app must be importable (entry point depends on it)."""
    from dvr.cli.main import app

    assert app is not None
    assert app.info.name == "dvr"


def test_connection_module_importable() -> None:
    """Importing connection should not connect to Resolve."""
    from dvr import connection

    assert callable(connection.connect)


def test_version_string() -> None:
    import dvr

    assert isinstance(dvr.__version__, str)
    assert dvr.__version__  # non-empty
