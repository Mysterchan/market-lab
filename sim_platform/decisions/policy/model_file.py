"""Model policy: trade on the output of a user-supplied Python model file.

The user's file runs in its own child process (see model_worker.py) and returns a
target position or explicit limit orders. Everything financial stays here, in the
platform: bars are aggregated from the recording, decimal prices are converted to
integer money, and a target position is turned into orders by one documented rule
(docs/user-guide/model-policies.md). The resulting actions are recorded with every
decision, so Replay never needs to run the model again.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import queue
import subprocess
import sys
from tempfile import TemporaryFile
from threading import Thread
from typing import IO, TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from sim_platform.domain.json_values import copy_json
from dataclasses import asdict

from sim_platform.domain.models import ContractError, Intent, MarketOrder, integer
from sim_platform.market.models import DAY_NS, MarketSnapshot, PriceVolumeBar, calendar_date
from sim_platform.decisions.policy.models import Action, Observation, PolicyResponse, submit

if TYPE_CHECKING:  # Annotation only.
    pass

# Money is integer USD ten-thousandths.
MONEY_SCALE = 10_000
MINUTE_NS = 60_000_000_000
# A daily price bar is stored as four one-minute prints: open, low/high, high/low, close.
DAILY_PRINTS = 4
DEFAULT_LOOKBACK_BARS = 50
MAX_LOOKBACK_BARS = 1000
# How long the model may take to import and run setup(); decisions use timeout_ms.
DEFAULT_STARTUP_SECONDS = 60.0
# The rule recorded with every run that a target position is turned into orders by.
TARGET_ORDER_RULE = 'cancel-own-open-then-limit-at-touch'
# Client IDs of orders this policy creates; used to find its own open orders.
CLIENT_PREFIX = 'model-'
OPEN_STATUSES = ('accepted', 'partial')
WORKER = Path(__file__).with_name('model_worker.py')


class BarSpec:
    """How the model's bars are cut from a recording: their length and lookback.

    A recording prepared from daily bars gives one bar per calendar day. One with
    intraday bars that are whole minutes long gives bars of that length. Any other
    recording, including ITCH and sub-minute bar files, gives one-minute bars.
    """

    def __init__(self, calendar: Optional[Dict[str, Any]], lookback: int) -> None:
        integer(lookback, 'lookback bars', 1)
        if lookback > MAX_LOOKBACK_BARS:
            raise ContractError('lookback bars must be at most ' + str(MAX_LOOKBACK_BARS))
        self.lookback = lookback
        self.start_date = (calendar or {}).get('start_date')
        timeframe = (calendar or {}).get('timeframe')
        seconds = int((calendar or {}).get('bar_seconds') or 60)
        if timeframe == '1d':
            self.label, self.span_ns, self.minutes = '1d', DAY_NS, DAILY_PRINTS
        elif calendar and seconds % 60 == 0:
            self.label, self.span_ns, self.minutes = f'{seconds}s', seconds * 10**9, seconds // 60
        else:
            self.label, self.span_ns, self.minutes = '60s', MINUTE_NS, 1

    def completed_by_symbol(self, market: Any) -> Dict[str, Tuple[PriceVolumeBar, ...]]:
        """Completed bars of every symbol of a merged market, at the merged time."""
        return {
            symbol: self.completed(market.symbol_market(symbol), market.time_ns)
            for symbol in market.universe
        }

    @classmethod
    def recorded(cls, parameters: Dict[str, Any]) -> BarSpec:
        """Rebuild the bar settings a model run recorded, for Replay."""
        label, lookback = parameters['bar'], parameters['lookback_bars']
        if label == '1d':
            return cls(dict(timeframe='1d'), lookback)
        return cls(dict(timeframe=label, bar_seconds=int(label[:-1])), lookback)

    def key(self, time_ns: int) -> int:
        """Which bar a market time belongs to."""
        return time_ns // self.span_ns

    def completed(self, market: Any, now: Optional[int] = None) -> Tuple[PriceVolumeBar, ...]:
        """The last ``lookback`` completed bars visible now, oldest first.

        The bar still in progress is left out, so a model never sees part of a bar.
        ``now`` is the experiment's time when ``market`` is one symbol of several.
        """
        now = market.time_ns if now is None else now
        minutes = market.bars(limit=(self.lookback + 1) * self.minutes)
        groups: Dict[int, List[PriceVolumeBar]] = {}
        for bar in minutes:
            groups.setdefault(self.key(bar.start_ns), []).append(bar)
        current = self.key(now)
        bars = [aggregate(groups[key], now) for key in sorted(groups) if key < current]
        return tuple(bars[-self.lookback :])


def aggregate(minutes: List[PriceVolumeBar], now: int) -> PriceVolumeBar:
    """Combine consecutive one-minute bars into one bar."""
    return PriceVolumeBar(
        start_ns=minutes[0].start_ns,
        end_ns=minutes[-1].end_ns,
        observed_through_ns=now,
        complete=True,
        open=minutes[0].open,
        high=max(b.high for b in minutes),
        low=min(b.low for b in minutes),
        close=minutes[-1].close,
        volume=sum(b.volume for b in minutes),
        count=sum(b.count for b in minutes),
        notional=sum(b.notional for b in minutes),
    )


def usd(value: int) -> float:
    """Integer money to a float in dollars, for the model only (never for accounting)."""
    return value / MONEY_SCALE


def money(value: Any, name: str) -> int:
    """A decimal dollar amount from the model, rounded half-even to a ten-thousandth."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ContractError(name + ' must be a number of dollars')
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ContractError(name + ' must be a number of dollars') from exc
    if not amount.is_finite():
        raise ContractError(name + ' must be finite')
    return int((amount * MONEY_SCALE).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def shares(value: Any, name: str) -> int:
    """A whole number of shares from the model; 3.0 is accepted, 3.5 is not."""
    if isinstance(value, bool):
        raise ContractError(name + ' must be a whole number of shares')
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if type(value) is not int:
        raise ContractError(name + ' must be a whole number of shares')
    return value


class ModelBridge:
    """The running model child process and its request pipe; owned by one policy.

    It starts on first use and stops on close() or when the platform exits, because
    end of input ends the worker. Requests are answered one at a time.
    """

    def __init__(
        self, model: Path, python: str, parameters: Dict[str, Any], startup_seconds: float
    ) -> None:
        self.model, self.python = model, python
        self.parameters, self.startup_seconds = parameters, startup_seconds
        self._process: Optional[subprocess.Popen[str]] = None
        self._errors: Optional[IO[bytes]] = None
        self._lines: queue.Queue[Optional[str]] = queue.Queue()

    def _read(self, stream: IO[str]) -> None:
        for line in stream:
            self._lines.put(line)
        self._lines.put(None)

    def _receive(self, timeout: float) -> Dict[str, Any]:
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            self.close()
            raise ContractError('model did not answer within ' + str(timeout) + ' s') from None
        if line is None:
            detail = self.error_output()
            self.close()
            raise ContractError('model process exited' + (': ' + detail if detail else ''))
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ContractError('model process sent an invalid message')
        return value

    def error_output(self) -> str:
        """The last part of what the model wrote to standard error."""
        if self._errors is None:
            return ''
        self._errors.seek(0)
        return self._errors.read()[-2000:].decode('utf-8', 'replace').strip()

    def start(self) -> None:
        if self._process is not None:
            return
        self._errors = TemporaryFile()
        self._process = subprocess.Popen(
            [self.python, '-I', '-u', str(WORKER), str(self.model)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._errors,
            text=True,
            encoding='utf-8',
            cwd=str(self.model.parent),
        )
        assert self._process.stdin is not None and self._process.stdout is not None
        Thread(target=self._read, args=(self._process.stdout,), daemon=True).start()
        self._process.stdin.write(json.dumps(dict(parameters=self.parameters)) + '\n')
        self._process.stdin.flush()
        ready = self._receive(self.startup_seconds)
        if not ready.get('ready'):
            self.close()
            raise ContractError('model failed to load: ' + str(ready.get('error')))

    def request(self, value: Dict[str, Any], timeout: float) -> Any:
        """Send one decision request and wait up to ``timeout`` seconds for its result."""
        self.start()
        assert self._process is not None and self._process.stdin is not None
        try:
            self._process.stdin.write(json.dumps(value, allow_nan=False) + '\n')
            self._process.stdin.flush()
        except OSError as exc:
            detail = self.error_output()
            self.close()
            raise ContractError('model process is not running: ' + detail) from exc
        answer = self._receive(timeout)
        if not answer.get('ok'):
            raise ContractError('model decide() failed: ' + str(answer.get('error')))
        return answer.get('result')

    def close(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            try:
                if process.stdin is not None:
                    process.stdin.close()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                process.wait()
            if process.stdout is not None:
                process.stdout.close()
        if self._errors is not None and process is not None:
            self._errors.close()
            self._errors = None


class ModelFilePolicy:
    """Decision method that asks a user's model file for a target position or orders.

    Owns one ModelBridge (the model's child process); close() stops it. Decisions are
    due once per bar (see BarSpec), at the first market event of a new bar, and the
    model sees only completed bars. The policy's identity, recorded with the run,
    includes the file's SHA-256, the bar settings and the target-order rule.

    Args:
        model_file: The user's Python file defining decide(bars, account, context).
        calendar: The prepared recording's calendar entry, or None for ITCH.
        lookback: Completed bars passed to each call.
        parameters: JSON values passed to the model's optional setup(parameters) and
            to every call as context['parameters'].
        python: Interpreter that runs the model; the App's own Python by default.
        startup_seconds: Longest time importing the file and setup() may take.
        allow_short: Whether the run allows short selling, so a target below zero is valid.
    """

    kind, name, version = 'model', 'model-file', '1'
    requires: Tuple[str, ...] = ('book', 'account', 'bars')

    def __init__(
        self,
        model_file: Any,
        calendar: Optional[Dict[str, Any]] = None,
        *,
        lookback: int = DEFAULT_LOOKBACK_BARS,
        parameters: Optional[Dict[str, Any]] = None,
        python: Optional[str] = None,
        startup_seconds: float = DEFAULT_STARTUP_SECONDS,
        allow_short: bool = False,
    ) -> None:
        path = Path(model_file).expanduser()
        if not path.is_absolute() or path.suffix != '.py' or not path.is_file():
            raise ContractError('model file must be an absolute path to an existing .py file')
        self.model_file = path
        self.bar_spec = BarSpec(calendar, lookback)
        self.model_parameters = copy_json(parameters or {})
        if not isinstance(self.model_parameters, dict):
            raise ContractError('model parameters must be a JSON object')
        self.python = python or sys.executable
        self.digest = sha256(path.read_bytes()).hexdigest()
        self._bridge = ModelBridge(path, self.python, self.model_parameters, startup_seconds)
        self._last_key: Optional[int] = None
        self.allow_short = allow_short

    @property
    def parameters(self) -> Dict[str, Any]:
        return dict(
            model_file=str(self.model_file),
            model_sha256=self.digest,
            bar=self.bar_spec.label,
            lookback_bars=self.bar_spec.lookback,
            model_parameters=self.model_parameters,
            target_order_rule=TARGET_ORDER_RULE,
        )

    def decision_due(self, time_ns: int) -> bool:
        """True at the first market event of a bar this policy has not decided on yet."""
        return self._last_key is None or self.bar_spec.key(time_ns) != self._last_key

    def _account(self, observation: Observation) -> Dict[str, Any]:
        market, execution = observation.market, observation.execution
        account = execution.account
        held = sum(p.quantity for p in account.positions if p.symbol == market.symbol)
        cost = sum(p.cost for p in account.positions if p.symbol == market.symbol)
        mark = market.bids[0][0] if market.bids else None
        return dict(
            cash=usd(account.cash),
            available_cash=usd(execution.available_cash),
            position=held,
            average_cost=usd(cost // held) if held else None,
            equity=usd(account.cash + held * mark) if mark is not None else None,
            open_orders=[
                dict(
                    client_id=o.intent_id.split(':intent:', 1)[-1],
                    side=o.side,
                    quantity=o.quantity,
                    filled=o.filled,
                    limit_price=usd(o.limit_price),
                )
                for o in account.orders
                if o.status in OPEN_STATUSES and o.symbol == market.symbol
            ],
        )

    def _context(self, observation: Observation) -> Dict[str, Any]:
        market = observation.market
        return dict(
            symbol=market.symbol,
            time_ns=market.time_ns,
            date=(
                calendar_date(self.bar_spec.start_date, market.time_ns)
                if self.bar_spec.start_date
                else None
            ),
            bar=self.bar_spec.label,
            bid=usd(market.bids[0][0]) if market.bids else None,
            ask=usd(market.asks[0][0]) if market.asks else None,
            parameters=copy_json(self.model_parameters),
        )

    def _bars(self, observation: Observation) -> List[Dict[str, Any]]:
        return [
            dict(
                time_ns=b.start_ns,
                date=(
                    calendar_date(self.bar_spec.start_date, b.start_ns)
                    if self.bar_spec.start_date
                    else None
                ),
                open=usd(b.open),
                high=usd(b.high),
                low=usd(b.low),
                close=usd(b.close),
                volume=b.volume,
            )
            for b in observation.bars or ()
        ]

    def _bar_rows(self, bars: Sequence[PriceVolumeBar]) -> List[Dict[str, Any]]:
        start = self.bar_spec.start_date
        return [
            dict(
                time_ns=b.start_ns,
                date=calendar_date(start, b.start_ns) if start else None,
                open=usd(b.open),
                high=usd(b.high),
                low=usd(b.low),
                close=usd(b.close),
                volume=b.volume,
            )
            for b in bars
        ]

    def _several(self, observation: Observation) -> Dict[str, Any]:
        """The request for an experiment on several symbols: everything by symbol."""
        execution, account = observation.execution, observation.execution.account
        books = {m.symbol: m for m in observation.markets or ()}
        held = {p.symbol: p.quantity for p in account.positions}
        cost = {p.symbol: p.cost for p in account.positions}
        marks = {
            symbol: (book.bids if held.get(symbol, 0) >= 0 else book.asks)
            for symbol, book in books.items()
        }
        marked = all(marks[s] for s in held if held[s])
        return dict(
            bars={s: self._bar_rows(b) for s, b in (observation.bars_by_symbol or {}).items()},
            account=dict(
                cash=usd(account.cash),
                available_cash=usd(execution.available_cash),
                positions={s: held.get(s, 0) for s in books},
                average_costs={s: usd(cost[s] // held[s]) if held.get(s) else None for s in books},
                equity=(
                    usd(account.cash + sum(q * marks[s][0][0] for s, q in held.items() if q))
                    if marked
                    else None
                ),
                open_orders=[
                    dict(
                        client_id=o.intent_id.split(':intent:', 1)[-1],
                        symbol=o.symbol,
                        side=o.side,
                        quantity=o.quantity,
                        filled=o.filled,
                        limit_price=usd(o.limit_price),
                    )
                    for o in account.orders
                    if o.status in OPEN_STATUSES
                ],
            ),
            context=dict(
                symbols=list(books),
                time_ns=observation.market.time_ns,
                date=(
                    calendar_date(self.bar_spec.start_date, observation.market.time_ns)
                    if self.bar_spec.start_date
                    else None
                ),
                bar=self.bar_spec.label,
                quotes={
                    s: dict(
                        bid=usd(b.bids[0][0]) if b.bids else None,
                        ask=usd(b.asks[0][0]) if b.asks else None,
                    )
                    for s, b in books.items()
                },
                parameters=copy_json(self.model_parameters),
            ),
        )

    def decide(self, observation: Observation, state: Any, timeout_ms: int) -> PolicyResponse:
        self._last_key = self.bar_spec.key(observation.market.time_ns)
        if observation.markets:
            result = self._bridge.request(self._several(observation), timeout_ms / 1000)
            actions = several_actions(result, observation, self.allow_short)
            return dict(actions=actions, state=state)
        result = self._bridge.request(
            dict(
                bars=self._bars(observation),
                account=self._account(observation),
                context=self._context(observation),
            ),
            timeout_ms / 1000,
        )
        return dict(actions=model_actions(result, observation, self.allow_short), state=state)

    def close(self) -> None:
        self._bridge.close()


def own_open_orders(observation: Observation, symbol: Optional[str] = None) -> List[str]:
    """Client IDs of this policy's orders that are still open, of one symbol if given."""
    ids = []
    for order in observation.execution.account.orders:
        client = order.intent_id.split(':intent:', 1)[-1]
        if (
            order.status in OPEN_STATUSES
            and client.startswith(CLIENT_PREFIX)
            and (symbol is None or order.symbol == symbol)
        ):
            ids.append(client)
    return ids


def target_actions(
    target: int,
    observation: Observation,
    allow_short: bool = False,
    market: Optional[MarketSnapshot] = None,
) -> List[Action]:
    """Turn a target position into actions by the documented rule.

    Cancel this policy's own open orders, then send one limit order for the difference
    between the target and the shares held: a buy at the best ask or a sell at the best
    bid. With no ask (or bid) shown, nothing is sent. Targets below zero need a run
    that allows short selling.
    """
    if target < 0 and not allow_short:
        raise ContractError('target_position below zero needs an experiment that allows shorts')
    several = market is not None
    market = market or observation.market
    actions: List[Action] = [
        dict(kind='cancel', client_id=client)
        for client in own_open_orders(observation, market.symbol if several else None)
    ]
    held = sum(
        p.quantity for p in observation.execution.account.positions if p.symbol == market.symbol
    )
    change = target - held
    client = CLIENT_PREFIX + str(observation.boundary) + (f'-{market.symbol}' if several else '')
    if change > 0 and market.asks:
        actions.append(submit(Intent(client, market.symbol, 'buy', change, market.asks[0][0])))
    elif change < 0 and market.bids:
        actions.append(submit(Intent(client, market.symbol, 'sell', -change, market.bids[0][0])))
    return actions


def order_actions(result: Dict[str, Any], observation: Observation) -> List[Action]:
    """Explicit limit orders from the model, optionally after cancelling its open ones."""
    orders = result.get('orders', [])
    cancel = result.get('cancel_open_orders', False)
    if not isinstance(orders, list) or type(cancel) is not bool:
        raise ContractError('orders must be a list and cancel_open_orders a boolean')
    actions: List[Action] = (
        [dict(kind='cancel', client_id=c) for c in own_open_orders(observation)] if cancel else []
    )
    for number, order in enumerate(orders):
        client = f'{CLIENT_PREFIX}{observation.boundary}-{number}'
        if isinstance(order, dict) and order.get('type') == 'market':
            if set(order) != {'side', 'quantity', 'type'}:
                raise ContractError('a market order needs exactly side, quantity and type')
            market_order = MarketOrder(
                client,
                observation.market.symbol,
                order['side'],
                shares(order['quantity'], 'order quantity'),
            )
            market_order.validate()
            actions.append(dict(kind='submit_market', order=asdict(market_order)))
            continue
        if not isinstance(order, dict) or set(order) != {'side', 'quantity', 'limit_price'}:
            raise ContractError('each order needs exactly side, quantity and limit_price')
        intent = Intent(
            client,
            observation.market.symbol,
            order['side'],
            shares(order['quantity'], 'order quantity'),
            money(order['limit_price'], 'limit_price'),
        )
        intent.validate()
        actions.append(submit(intent))
    return actions


def model_actions(result: Any, observation: Observation, allow_short: bool = False) -> List[Action]:
    """Validate what decide() returned and turn it into policy actions."""
    if result is None:
        return []
    if not isinstance(result, dict):
        raise ContractError('decide() must return None or a dict')
    if 'target_position' in result:
        if set(result) != {'target_position'}:
            raise ContractError('target_position cannot be combined with other fields')
        target = shares(result['target_position'], 'target_position')
        return target_actions(target, observation, allow_short)
    if set(result) - {'orders', 'cancel_open_orders'}:
        raise ContractError('decide() returned unknown fields: ' + ', '.join(sorted(result)))
    return order_actions(result, observation)


def several_actions(result: Any, observation: Observation, allow_short: bool) -> List[Action]:
    """Actions from a model on several symbols: targets by symbol, or orders with a symbol.

    ``{"targets": {"AAPL": 100, "MSFT": 0}}`` applies the target rule to each symbol in
    the order given; orders must name their ``symbol``.
    """
    if result is None:
        return []
    if not isinstance(result, dict):
        raise ContractError('decide() must return None or a dict')
    books = {m.symbol: m for m in observation.markets or ()}
    if 'targets' in result:
        targets = result['targets']
        if set(result) != {'targets'} or not isinstance(targets, dict):
            raise ContractError('targets must be the only field: a mapping of symbol to shares')
        actions: List[Action] = []
        for symbol, target in targets.items():
            if symbol not in books:
                raise ContractError('target symbol is outside the experiment: ' + str(symbol))
            actions += target_actions(
                shares(target, 'target for ' + symbol), observation, allow_short, books[symbol]
            )
        return actions
    if set(result) - {'orders', 'cancel_open_orders'}:
        raise ContractError('decide() returned unknown fields: ' + ', '.join(sorted(result)))
    orders = result.get('orders', [])
    if not isinstance(orders, list) or any(
        not isinstance(o, dict) or o.get('symbol') not in books for o in orders
    ):
        raise ContractError('each order needs a symbol of the experiment')
    actions = (
        [dict(kind='cancel', client_id=c) for c in own_open_orders(observation)]
        if result.get('cancel_open_orders') is True
        else []
    )
    for number, order in enumerate(orders):
        single = dict(orders=[{k: v for k, v in order.items() if k != 'symbol'}])
        for action in order_actions(single, observation):
            key = 'intent' if action['kind'] == 'submit' else 'order'
            action[key].update(
                symbol=order['symbol'],
                client_id=f'{CLIENT_PREFIX}{observation.boundary}-{order["symbol"]}-{number}',
            )
            actions.append(action)
    return actions
