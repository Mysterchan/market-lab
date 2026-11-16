"""Stable entry point for python -m sim_platform."""

from .cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
