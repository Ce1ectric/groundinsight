# groundinsight_gui/__init__.py
"""
groundinsight-gui -- the desktop and browser front end of groundinsight.

A distribution of its own (``pip install groundinsight-gui``), released with
the library and carrying its version. It lives outside ``src/groundinsight``
so that the library stays free of UI dependencies: ``pytest
--doctest-modules`` over the library would otherwise import NiceGUI in every
run, and a server or a notebook would install a web framework it never uses.

Rules (``tests/test_rules.py`` enforces them): this package imports only
``groundinsight.api`` and the top-level ``groundinsight`` namespace; every
number it shows comes from a DTO of the service layer and is only formatted
here; long work runs as a job of the service layer, never in the event loop.

Start it with ``python -m groundinsight_gui`` (browser on 127.0.0.1) or
``python -m groundinsight_gui --native`` (a desktop window through
pywebview, extra ``native``).
"""

__all__ = ["__version__"]

#: The distribution's version; the release script keeps it equal to the
#: library's (gui/pyproject.toml).
__version__ = "0.6.0"
