"""Trusted local policies and a timeout-enforced JSON subprocess entry point."""

from __future__ import annotations

import json
import subprocess
from tempfile import TemporaryFile
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sim_platform.domain.models import ContractError, Intent, integer, label
from sim_platform.domain.json_values import copy_json, encode
from sim_platform.decisions.policy.models import (
    DEFAULT_HOLD_NS,
    DEFAULT_RULE_QUANTITY,
    MAX_RESPONSE_BYTES,
    Action,
    Observation,
    PolicyResponse,
    fingerprint,
    submit,
)

# HoldThenSell's own default holding time when constructed directly (1 s).
HOLD_THEN_SELL_DEFAULT_NS = 1_000_000_000


class HumanPolicy:
    """Placeholder for manual decisions: never acts on its own and keeps state unchanged."""

    kind, name, version = 'human', 'manual', '1'
    requires: Tuple[str, ...] = ('book', 'account')

    def decide(self, observation: Observation, state: Any, timeout_ms: int) -> PolicyResponse:
        return {'actions': [], 'state': state}


def _per_symbol(observation: Any, state: Any, decide_one: Any) -> PolicyResponse:
    """Run a one-book rule on every book of a run on several symbols.

    Each symbol keeps its own state under its name, and its orders carry the symbol in
    their client IDs, so the symbols never share a decision. ``decide_one(book, state,
    suffix)`` returns (actions, state) for one book.
    """
    state = copy_json(state) or {}
    actions: List[Action] = []
    for book in observation.markets:
        found, state[book.symbol] = decide_one(book, state.get(book.symbol) or {}, book.symbol)
        actions.extend(found)
    return {'actions': actions, 'state': state}


class BuyHoldSell:
    """Rule: buy once at the best ask, then sell everything at the best bid after hold_ns.

    Stateless between calls apart from the JSON state it returns ('buy_sent',
    'held_since', 'sell_sent').
    """

    kind, name, version = 'rule', 'buy-hold-sell', '1'
    requires: Tuple[str, ...] = ('book', 'account')

    def __init__(
        self, quantity: int = DEFAULT_RULE_QUANTITY, hold_ns: int = DEFAULT_HOLD_NS
    ) -> None:
        integer(quantity, 'rule quantity', 1)
        integer(hold_ns, 'holding time')
        self.quantity, self.hold_ns = quantity, hold_ns

    @property
    def parameters(self) -> Dict[str, Any]:
        return {'quantity': self.quantity, 'hold_ns': self.hold_ns}

    def decide(self, observation: Observation, state: Any, timeout_ms: int) -> PolicyResponse:
        account = observation.execution.account
        if getattr(observation, 'markets', None):
            return _per_symbol(
                observation,
                state,
                lambda book, own, suffix: self._one(book, account, own, '-' + suffix),
            )
        actions, state = self._one(observation.market, account, copy_json(state), '')
        return {'actions': actions, 'state': state}

    def _one(self, book: Any, account: Any, state: Any, suffix: str) -> Tuple[List[Action], Any]:
        """The rule for one book; ``suffix`` keeps client IDs distinct per symbol."""
        actions: List[Action] = []
        quantity = sum(p.quantity for p in account.positions if p.symbol == book.symbol)
        if not state.get('buy_sent') and book.bids and book.asks:
            actions.append(
                submit(
                    Intent('rule-buy' + suffix, book.symbol, 'buy', self.quantity, book.asks[0][0])
                )
            )
            state['buy_sent'] = True
        if quantity and 'held_since' not in state:
            state['held_since'] = book.time_ns
        if (
            quantity
            and book.bids
            and not state.get('sell_sent')
            and book.time_ns - state['held_since'] >= self.hold_ns
        ):
            actions.append(
                submit(Intent('rule-sell' + suffix, book.symbol, 'sell', quantity, book.bids[0][0]))
            )
            state['sell_sent'] = True
        return actions, state


class HoldThenSell:
    """Manage an inherited holding without adding a new buy."""

    kind, name, version = 'rule', 'hold-then-sell', '1'
    requires: Tuple[str, ...] = ('book', 'account')

    def __init__(self, hold_ns: int = HOLD_THEN_SELL_DEFAULT_NS) -> None:
        integer(hold_ns, 'holding time')
        self.hold_ns = hold_ns

    @property
    def parameters(self) -> Dict[str, Any]:
        return {'hold_ns': self.hold_ns}

    def decide(self, observation: Observation, state: Any, timeout_ms: int) -> PolicyResponse:
        if getattr(observation, 'markets', None):
            return _per_symbol(
                observation,
                state,
                lambda book, own, suffix: self._one(observation, book, own, '-' + suffix),
            )
        actions, state = self._one(observation, observation.market, copy_json(state), '')
        return {'actions': actions, 'state': state}

    def _one(
        self, observation: Any, book: Any, state: Any, suffix: str
    ) -> Tuple[List[Action], Any]:
        """Sell one book's inherited holding after the hold; ``suffix`` marks its symbol."""
        quantity = sum(
            p.quantity for p in observation.execution.account.positions if p.symbol == book.symbol
        )
        actions: List[Action] = []
        if quantity:
            state.setdefault('held_since', book.time_ns)
            reserved = dict(observation.execution.reserved_shares).get(book.symbol, 0)
            if (
                not state.get('sell_sent')
                and book.bids
                and quantity > reserved
                and book.time_ns - state['held_since'] >= self.hold_ns
            ):
                # Boundary makes new orders from later reset generations use distinct client IDs.
                actions.append(
                    submit(
                        Intent(
                            f'hold-sell-{observation.boundary}{suffix}',
                            book.symbol,
                            'sell',
                            quantity - reserved,
                            book.bids[0][0],
                        )
                    )
                )
                state['sell_sent'] = True
        return actions, state


class JSONProcessPolicy:
    """Caller-authorized command; subprocess isolation is not a security sandbox.

    stdin: {observation, state}; stdout: {actions, state}; one invocation per decision.
    Timeout kills/waits the direct child. Outputs are disk-backed and size-checked.
    """

    requires: Tuple[str, ...] = ('book', 'account')

    def __init__(
        self,
        command: Sequence[str],
        *,
        kind: str = 'model',
        name: str = 'json-process',
        version: str = '1',
        requires: Optional[Sequence[str]] = None,
    ) -> None:
        if kind not in ('model', 'agent', 'rule'):
            raise ContractError('invalid external policy kind')
        if (
            not isinstance(command, (list, tuple))
            or not command
            or any(not isinstance(arg, str) or not arg for arg in command)
        ):
            raise ContractError('external command must be an argument list')
        label(name, 'policy name')
        label(version, 'policy version')
        self.command, self.kind, self.name, self.version = tuple(command), kind, name, version
        if requires is not None:
            self.requires = tuple(requires)

    @property
    def parameters(self) -> Dict[str, Any]:
        return {'command': list(self.command)}

    def decide(self, observation: Observation, state: Any, timeout_ms: int) -> Any:
        """Run the command once; returns its decoded stdout, validated later by the caller."""
        request = encode({'observation': observation.to_json(), 'state': state}).encode()
        with TemporaryFile() as output, TemporaryFile() as errors:
            subprocess.run(
                self.command,
                input=request,
                stdout=output,
                stderr=errors,
                timeout=timeout_ms / 1000,
                check=True,
            )
            if output.tell() > MAX_RESPONSE_BYTES:
                raise ContractError('external response exceeds 64 KiB')
            output.seek(0)
            return json.loads(output.read().decode('utf-8'))


class RecordedPolicy:
    """Replay actions; compare the complete visible observation before returning them.

    The instance owns a read cursor over the recording; call assert_consumed() after
    the run to confirm every recorded decision was replayed.
    """

    name, version = 'recorded-actions', '1'
    requires: Tuple[str, ...] = ('book', 'account')

    def __init__(self, records: List[Dict[str, Any]], kind: str = 'rule') -> None:
        self.records, self.kind, self.index = copy_json(records), kind, 0

    def decide(self, observation: Observation, state: Any, timeout_ms: int) -> PolicyResponse:
        if self.index >= len(self.records):
            raise ContractError('recording exhausted')
        record = self.records[self.index]
        if record['fingerprint'] != fingerprint(observation.to_json()):
            raise ContractError('recorded observation mismatch')
        if record['status'] != 'ok':
            raise ContractError('cannot replay a failed decision as successful actions')
        self.index += 1
        return copy_json(record['response'])

    @property
    def parameters(self) -> Dict[str, str]:
        from hashlib import sha256

        return {'recording_sha256': sha256(encode(self.records).encode()).hexdigest()}

    def assert_consumed(self) -> None:
        if self.index != len(self.records):
            raise ContractError('unconsumed recorded decisions')
