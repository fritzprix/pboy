"""CLI module for Pageboy.

Note: do not re-export ``main`` here — that shadows the ``pboy.cli.main`` submodule.
Entry point remains ``pboy.cli.main:main`` in pyproject.toml.
"""

from pboy.cli.main import app

__all__ = ["app"]
