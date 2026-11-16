"""Shared App/CLI storage resolution; importing this module creates no files."""

import os
from pathlib import Path
import sys


def data_directory() -> Path:
    """Resolve a per-OS-user location, independent of the working directory.

    macOS sandbox deployments must supply their container location through the
    explicit override. The default location is for a local application outside an OS sandbox.
    """
    override = os.environ.get("MARKET_LAB_DATA_DIR")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("MARKET_LAB_DATA_DIR must be absolute")
        return path
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "MarketLab"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise ValueError("LOCALAPPDATA is unavailable; supply an explicit data directory")
        return Path(base) / "MarketLab"
    base = os.environ.get("XDG_DATA_HOME")
    if base and not Path(base).is_absolute():
        raise ValueError("XDG_DATA_HOME must be absolute")
    return (Path(base) if base else Path.home() / ".local" / "share") / "MarketLab"
