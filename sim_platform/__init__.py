"""Local account core and independent market module; no Qt or broker dependency."""

import logging as _logging

from sim_platform.domain.models import (
    ContractError,
    AccountSnapshot,
    Intent,
    OrderSnapshot,
    Position,
    MONEY_SCALE,
    Profile,
    SimulationAccount,
    DecisionMaker,
    Run,
    LedgerEvent,
)

__version__ = "1.0.0"

# A library prints nothing by default; see sim_platform.diagnostics.configure_logging().
_logging.getLogger(__name__).addHandler(_logging.NullHandler())

__all__ = [
    "Platform",
    "DecisionSession",
    "ContractError",
    "AccountSnapshot",
    "Intent",
    "OrderSnapshot",
    "Position",
    "MONEY_SCALE",
    "Profile",
    "SimulationAccount",
    "DecisionMaker",
    "Run",
    "LedgerEvent",
]


def __getattr__(name):
    # Importing domain or market contracts must not initialize the ORM dependency.
    if name in ('Platform', 'DecisionSession'):
        from sim_platform.application.platform import Platform, DecisionSession

        globals().update(Platform=Platform, DecisionSession=DecisionSession)
        return globals()[name]
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
