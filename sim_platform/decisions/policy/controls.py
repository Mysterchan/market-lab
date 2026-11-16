"""Policy-specific research controls; common experiment control stays on the runner."""

from __future__ import annotations

from typing import TYPE_CHECKING, ContextManager, Dict, Optional

from sim_platform.domain.models import ContractError, Intent
from sim_platform.domain.json_values import encode
from sim_platform.resources import serialized
from sim_platform.decisions.policy.contracts import make_policy, policy_descriptor
from sim_platform.decisions.policy.models import DEFAULT_HOLD_NS, DEFAULT_RULE_QUANTITY, submit

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.decisions.policy.contracts import Policy
    from sim_platform.decisions.policy.handoffs import HandoffCondition
    from sim_platform.decisions.policy.models import MarketInput
    from sim_platform.decisions.policy.runner import PolicyDecisionRunner


class PolicyControls:
    """Human orders and managed policy switches over the runner's authority boundary.

    Borrowed from and owned by one PolicyDecisionRunner. It remembers the maker created
    for each policy descriptor so switching back reuses the same maker.
    """

    def __init__(
        self, runner: PolicyDecisionRunner, market: MarketInput, maker: str, policy: Policy
    ) -> None:
        self._runner, self._market = runner, market
        # Maker ID per encoded policy descriptor.
        self._makers: Dict[str, str] = {encode(policy_descriptor(policy)): maker}

    def control_boundary(self) -> ContextManager[None]:
        return self._runner.control_boundary()

    def _check_active(self) -> None:
        if not self._runner.active:
            raise ContractError('no active policy simulation')

    @serialized
    def order(
        self,
        client_id: str,
        side: str,
        quantity: int,
        limit_price: int,
        symbol: Optional[str] = None,
    ) -> None:
        """Send a limit order; ``symbol`` defaults to the market's (current) symbol."""
        self._check_active()
        symbol = symbol or self._market.snapshot.symbol
        self._runner.controller().act(
            [submit(Intent(client_id, symbol, side, quantity, limit_price))]
        )

    @serialized
    def market_order(
        self, client_id: str, side: str, quantity: int, symbol: Optional[str] = None
    ) -> None:
        """Send a market order; it is priced at acceptance (docs/reference/orders.md)."""
        self._check_active()
        symbol = symbol or self._market.snapshot.symbol
        order = dict(client_id=client_id, symbol=symbol, side=side, quantity=quantity)
        self._runner.controller().act([dict(kind='submit_market', order=order)])

    @serialized
    def cancel(self, client_id: str) -> None:
        self._check_active()
        self._runner.controller().act([dict(kind='cancel', client_id=client_id)])

    @serialized
    def handoff(
        self,
        policy: str,
        quantity: int = DEFAULT_RULE_QUANTITY,
        hold_ns: int = DEFAULT_HOLD_NS,
        state_mode: str = 'reset',
        condition: Optional[HandoffCondition] = None,
    ) -> str:
        """Switch (or schedule a switch) to a built-in policy; returns its maker ID."""
        self._check_active()
        selected = make_policy(policy, quantity, hold_ns)
        key = encode(policy_descriptor(selected))
        maker = self._runner.managed_handoff(
            selected, self._makers.get(key), state_mode=state_mode, condition=condition
        )
        self._makers[key] = maker
        return maker
