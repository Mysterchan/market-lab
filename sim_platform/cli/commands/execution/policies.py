"""Policy construction shared by decision startup and handoff commands."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from sim_platform.decisions import DecisionConfig
from sim_platform.decisions.policy.contracts import make_policy as build_policy


if TYPE_CHECKING:  # Annotation only.
    from sim_platform.decisions.policy.contracts import Policy


def make_policy(name: str, args: argparse.Namespace) -> Policy:
    return build_policy(name, args.quantity, args.hold_ns)


def policy_config(name: str, args: argparse.Namespace) -> DecisionConfig:
    return DecisionConfig(
        mode='manual' if name == 'human' else 'automatic',
        interval_ns=args.interval_ns,
        timeout_ms=args.timeout_ms,
    )
