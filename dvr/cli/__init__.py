"""Command-line interface for `dvr`.

The CLI is a thin wrapper around the library: every command is a
``Resolve()`` call followed by an ``output()`` call. All formatting,
serialization, and error rendering lives in :mod:`dvr.cli.output`.
"""
