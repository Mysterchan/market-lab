"""Policy contract, validation and shared construction for every frontend."""

from __future__ import annotations

from typing import Protocol, Tuple

from sim_platform.domain.models import ContractError, label
from sim_platform.domain.json_values import copy_json
from sim_platform.decisions.policy.models import (
    CAPABILITIES,
    DEFAULT_HOLD_NS,
    DEFAULT_RULE_QUANTITY,
    Observation,
    DecisionConfig,
    PolicyDescriptor,
)


class Policy(Protocol):
    """What any decision policy provides: an identity and a pure decide() call."""

    kind: str
    name: str
    version: str
    requires: Tuple[str, ...]

    def decide(self, observation: Observation, state: object, timeout_ms: int) -> object: ...


def policy_descriptor(policy: Policy) -> PolicyDescriptor:
    """Validate a policy's identity and return it as a detached JSON descriptor."""
    if not callable(getattr(policy, 'decide', None)):
        raise ContractError('policy must provide decide(observation, state, timeout_ms)')
    kind = getattr(policy, 'kind', None)
    if kind not in ('human', 'rule', 'model', 'agent'):
        raise ContractError('invalid policy kind')
    name, version = getattr(policy, 'name', None), getattr(policy, 'version', None)
    label(name, 'policy name')
    label(version, 'policy version')
    requires = getattr(policy, 'requires', None)
    if not isinstance(requires, (list, tuple)) or any(c not in CAPABILITIES for c in requires):
        raise ContractError('required data capability is unavailable')
    return copy_json(
        dict(
            kind=kind,
            name=name,
            version=version,
            requires=list(requires),
            parameters=getattr(policy, 'parameters', {}),
        )
    )


def make_policy(
    name: str, quantity: int = DEFAULT_RULE_QUANTITY, hold_ns: int = DEFAULT_HOLD_NS
) -> Policy:
    """Build a built-in policy by its short name: 'human', 'rule' or 'hold'."""
    from sim_platform.decisions.policy.policies import HumanPolicy, BuyHoldSell, HoldThenSell

    if name == 'human':
        return HumanPolicy()
    if name == 'rule':
        return BuyHoldSell(quantity, hold_ns)
    if name == 'hold':
        return HoldThenSell(hold_ns)
    raise ContractError('policy must be human, rule or hold')


def default_config(policy: Policy) -> DecisionConfig:
    """Human policies wait for manual commands; every other kind decides automatically."""
    return DecisionConfig(mode='manual' if policy.kind == 'human' else 'automatic')
