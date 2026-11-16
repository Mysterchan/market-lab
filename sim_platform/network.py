"""Offline mode: refuse every action that would reach beyond this machine's network.

Set ``MARKET_LAB_OFFLINE=1`` (or call ``set_offline(True)``) for air-gapped research.
Market Lab then refuses the hosted model provider, Python runtime downloads and public
package downloads, so nothing leaves the machine; local model servers on loopback or a
private network, wheelhouses and offline Python archives keep working. Everything else
(markets, experiments, Replay, evaluation, reports, the Desktop) never uses the network.
"""

from __future__ import annotations

import os
from typing import Optional

from sim_platform.domain.models import ContractError

# Environment variable that turns offline mode on ('1', 'true' or 'yes').
OFFLINE_ENVIRONMENT = 'MARKET_LAB_OFFLINE'
_override: Optional[bool] = None


def set_offline(value: Optional[bool]) -> None:
    """Force offline mode on or off for this process; None follows the environment."""
    global _override
    _override = value


def offline() -> bool:
    """Whether offline mode is on."""
    if _override is not None:
        return _override
    return os.environ.get(OFFLINE_ENVIRONMENT, '').strip().lower() in ('1', 'true', 'yes')


def require_network(action: str) -> None:
    """Raise ContractError naming ``action`` when offline mode is on."""
    if offline():
        raise ContractError(
            f'{action} needs the network, but offline mode is on ({OFFLINE_ENVIRONMENT}); '
            'use a local model server, a wheelhouse or an offline Python archive'
        )
