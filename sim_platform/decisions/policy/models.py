"""Bounded decision contracts. Prices use the existing integer money scale."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, Union

from sim_platform.domain.models import ContractError, Intent, MarketOrder, integer, label
from sim_platform.domain.json_values import copy_json, encode
from sim_platform.market.models import MarketSnapshot, MarketTrade, NewsRecord, PriceVolumeBar
from sim_platform.execution.models import ExecutionSnapshot

if TYPE_CHECKING:  # Annotation only: the concrete market sessions a runner may drive.
    from sim_platform.market.playback import MarketPlayback
    from sim_platform.market.prepared.session import PreparedSession

    # A market a Controlled policy runner observes: live playback or a prepared session.
    MarketInput = Union[MarketPlayback, PreparedSession]


# 'bars' is the completed price bars a model policy sees; see model_file.BarSpec.
CAPABILITIES = ('book', 'account', 'trades', 'news', 'bars')

# A policy's encoded response (and an external process's output) may not exceed 64 KiB.
MAX_RESPONSE_BYTES = 65536
# Upper bounds accepted by DecisionConfig.
MAX_HISTORY_LIMIT = 1000
MAX_ACTIONS = 100
MAX_DECISIONS = 10000
# Built-in Rule defaults: shares bought, and nanoseconds held before selling (2 s).
DEFAULT_RULE_QUANTITY = 10
DEFAULT_HOLD_NS = 2_000_000_000

# One policy action: {'kind': 'submit', 'intent': <Intent fields>},
# {'kind': 'submit_market', 'order': <MarketOrder fields>} or
# {'kind': 'cancel', 'client_id': str}; trusted callers may cancel by 'order_id'.
Action = Dict[str, Any]
# A policy's reply: 'actions' (a list of Action) and 'state' (the next finite JSON state).
PolicyResponse = Dict[str, Any]
# The run's persisted decision journal: 'state', '_record_count', and optionally
# 'generation', 'handoffs', 'saved_states', 'switches', 'authority_rejections' and
# 'research_archive'.
DecisionJournal = Dict[str, Any]
# One audited decision: 'index', 'observation', 'maker_id', 'generation', 'fingerprint',
# 'status', 'error', 'response', 'attempted_response'; after commit also 'results' and,
# when known, 'position' and 'operation_position'.
DecisionRecord = Dict[str, Any]
# A policy identity: 'kind', 'name', 'version', 'requires' and 'parameters'.
PolicyDescriptor = Dict[str, Any]


@dataclass(frozen=True)
class DecisionConfig:
    """When a policy is invoked and how much it may do; validated on construction.

    ``interval_ns`` is the minimum market time between automatic decisions and
    ``timeout_ms`` the wall-clock budget for one policy call.
    """

    mode: str = 'automatic'
    triggers: Tuple[str, ...] = ('market', 'clock', 'news')
    interval_ns: int = 0
    timeout_ms: int = 1000
    max_actions: int = 8
    history_limit: int = 50
    max_decisions: int = MAX_DECISIONS

    def __post_init__(self) -> None:
        if self.mode not in ('automatic', 'manual'):
            raise ContractError('decision mode must be automatic or manual')
        if (
            not isinstance(self.triggers, (list, tuple))
            or not self.triggers
            or any(t not in ('market', 'clock', 'news') for t in self.triggers)
        ):
            raise ContractError('invalid decision triggers')
        object.__setattr__(self, 'triggers', tuple(self.triggers))
        integer(self.interval_ns, 'decision interval')
        for name in ('timeout_ms', 'max_actions', 'history_limit', 'max_decisions'):
            integer(getattr(self, name), name, 1)
        if (
            self.history_limit > MAX_HISTORY_LIMIT
            or self.max_actions > MAX_ACTIONS
            or self.max_decisions > MAX_DECISIONS
        ):
            raise ContractError('decision bounds exceed prototype limits')


@dataclass(frozen=True)
class Observation:
    """What a policy sees at one decision boundary; a detached, immutable value.

    ``boundary`` counts market events processed so far and ``trigger`` names the
    event kind (or 'manual') that caused this decision. ``bars`` holds completed price
    bars only for a policy that requires the 'bars' capability; otherwise it is None
    and absent from the JSON form, which keeps earlier recorded observations unchanged.
    ``markets`` holds every symbol's book in an experiment on several symbols, and is
    likewise None and absent otherwise.
    """

    boundary: int
    trigger: str
    market: MarketSnapshot
    execution: ExecutionSnapshot
    trades: Tuple[MarketTrade, ...]
    news: Tuple[NewsRecord, ...]
    bars: Optional[Tuple[PriceVolumeBar, ...]] = None
    markets: Optional[Tuple[MarketSnapshot, ...]] = None
    bars_by_symbol: Optional[Dict[str, Tuple[PriceVolumeBar, ...]]] = None

    def to_json(self) -> Dict[str, Any]:
        value = copy_json(asdict(self))
        for optional in ('bars', 'markets', 'bars_by_symbol'):
            if value[optional] is None:
                del value[optional]
        return value

    @property
    def universe(self) -> Tuple[str, ...]:
        """The symbols this observation covers."""
        return observed_universe(self)


def observed_universe(observation: Any) -> Tuple[str, ...]:
    """The symbols an observation covers; also accepts the Live child's plain values."""
    markets = getattr(observation, 'markets', None)
    if markets:
        return tuple(m.symbol for m in markets)
    return (observation.market.symbol,)


def fingerprint(observation: Any) -> str:
    """Compare replay inputs independent of generated account/run/order IDs."""

    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: (v.split(':intent:', 1)[-1] if k == 'intent_id' else normalize(v))
                for k, v in value.items()
                if k not in ('account_id', 'profile_id', 'run_id', 'order_id')
            }
        if isinstance(value, (tuple, list)):
            return [normalize(v) for v in value]
        return value

    return sha256(encode(normalize(observation)).encode()).hexdigest()


def validate_response(
    response: Any, observation: Observation, config: DecisionConfig
) -> PolicyResponse:
    """Return a detached copy of a policy reply, or raise ContractError if it is invalid."""
    value = copy_json(response)
    if not isinstance(value, dict) or set(value) != {'actions', 'state'}:
        raise ContractError('response must contain only actions and state')
    if not isinstance(value['actions'], list) or len(value['actions']) > config.max_actions:
        raise ContractError('action budget exceeded or actions is not a list')
    if len(encode(value).encode()) > MAX_RESPONSE_BYTES:
        raise ContractError('response exceeds 64 KiB')
    for action in value['actions']:
        if not isinstance(action, dict):
            raise ContractError('action must be an object')
        if action.get('kind') == 'submit' and set(action) == {'kind', 'intent'}:
            intent = Intent(**action['intent'])
            intent.validate()
            if intent.symbol not in observed_universe(observation):
                raise ContractError('action symbol is outside the observation universe')
        elif action.get('kind') == 'submit_market' and set(action) == {'kind', 'order'}:
            if not isinstance(action['order'], dict) or set(action['order']) != {
                'client_id',
                'symbol',
                'side',
                'quantity',
            }:
                raise ContractError('a market order needs client_id, symbol, side and quantity')
            order = MarketOrder(**action['order'])
            order.validate()
            if order.symbol not in observed_universe(observation):
                raise ContractError('action symbol is outside the observation universe')
        elif action.get('kind') == 'cancel' and set(action) == {'kind', 'client_id'}:
            label(action['client_id'], 'cancellation client ID')
        else:
            raise ContractError('unsupported action or extra response fields')
    return value


def submit(intent: Intent) -> Action:
    """Wrap an order intent as a submit action."""
    return {'kind': 'submit', 'intent': asdict(intent)}
