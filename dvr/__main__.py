"""Make ``python -m dvr`` an alias for the CLI."""

from __future__ import annotations

from .cli.main import main

if __name__ == "__main__":
    main()
