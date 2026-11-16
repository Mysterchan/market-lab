"""Shared visual values for native Desktop, its web workbench and documentation.

This module has no Qt, browser, storage or build-system dependency."""

from __future__ import annotations

from typing import Dict

_DARK = dict(
    background='#191c21',
    chrome='#14171b',
    sidebar='#16191e',
    surface='#1c2026',
    hover='#242a32',
    selected='#242f40',
    text='#dbe1eb',
    bright='#f3f5f9',
    muted='#b1bdce',
    faint='#7d899b',
    border='#2c323b',
    subtle='#242a32',
    accent='#8cacec',
    primary='#315fa8',
    primary_hover='#284f8c',
    on_primary='#ffffff',
    positive='#68c7ac',
    negative='#d58b93',
    warning='#c9b17a',
    positive_surface='#20362f',
    negative_surface='#38272e',
    brand_surface='#254e8b',
    on_brand='#ffffff',
    brand_secondary='#d5e2f7',
)
_LIGHT = dict(
    background='#ffffff',
    chrome='#f3f4f6',
    sidebar='#f7f8fa',
    surface='#fafbfd',
    hover='#edf0f4',
    selected='#e8edf6',
    text='#222936',
    bright='#101927',
    muted='#4f5d70',
    faint='#657389',
    border='#d4dae3',
    subtle='#f0f2f6',
    accent='#315fa8',
    primary='#315fa8',
    primary_hover='#284f8c',
    on_primary='#ffffff',
    positive='#16785e',
    negative='#b15963',
    warning='#805e22',
    positive_surface='#eaf5f0',
    negative_surface='#faedef',
    brand_surface='#254e8b',
    on_brand='#ffffff',
    brand_secondary='#d5e2f7',
)


def palette(light: bool = False) -> Dict[str, str]:
    """Return detached colours; changing a caller's mapping cannot alter the theme."""
    return dict(_LIGHT if light else _DARK)


# Role names stay stable even when a renderer uses a different unit system.
TYPE = dict(body=15, label=14, metadata=13, section=19, title=28, code=14)
SPACE = dict(tight=4, compact=8, control=12, group=16, section=24, page=32)
