"""Explicit execution mode shared by preparation and one-shot experiment setup."""

from .models import ContractError


def validate_execution_mode(value: object) -> str:
    """Accept only controlled/live; reject historical labels before creating a run."""
    if type(value) is not str or value not in ('controlled', 'live'):
        raise ContractError('execution_mode must be controlled or live')
    return value
