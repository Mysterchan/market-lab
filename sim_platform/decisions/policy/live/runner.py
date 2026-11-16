"""Common background policy facade; the market owner is injected by Platform.

Controlled policy machinery retains audit/authority/state semantics. User-facing
commands enqueue detached values; only the owner calls the controlled core.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, List, NoReturn, Optional

from sim_platform.decisions.base import DecisionRunner
from sim_platform.market.pacing import validate_poll
from sim_platform.domain.models import ContractError, Intent
from sim_platform.decisions.policy.models import submit
from sim_platform.domain.json_values import copy_json

from sim_platform.experiments.execution.controls import CONTROL_ACK_TIMEOUT_SECONDS

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.decisions.policy.live.owner import PolicyOwner
    from sim_platform.decisions.policy.models import Action, DecisionRecord
    from sim_platform.decisions.policy.runner import PolicyDecisionRunner
    from sim_platform.experiments.execution.controls import ExperimentControls
    from sim_platform.experiments.execution.session import ExperimentSession
    from sim_platform.experiments.recording import ExperimentLog
    from sim_platform.experiments.scheduling.host import ExperimentRuntimeHandle

# The detached view carries at most this many recent trades and price/volume bars.
VIEW_TRADE_LIMIT = 100
VIEW_BAR_LIMIT = 60


class LivePolicyControls:
    """Human order/cancel commands for a Live run; handoffs are refused in Live mode.

    Borrowed from the Live runner, which owns it; holds no resources.
    """

    def __init__(self, runner: LivePolicyDecisionRunner, symbol: str) -> None:
        self.runner, self.symbol = runner, symbol

    def order(
        self,
        client_id: str,
        side: str,
        quantity: int,
        limit_price: int,
        symbol: Optional[str] = None,
    ) -> DecisionRecord:
        """Send a limit order; ``symbol`` chooses one symbol of a run on several."""
        return self.runner.act(
            [submit(Intent(client_id, symbol or self.symbol, side, quantity, limit_price))]
        )

    def market_order(
        self, client_id: str, side: str, quantity: int, symbol: Optional[str] = None
    ) -> DecisionRecord:
        """Send a market order; it is priced at acceptance (docs/reference/orders.md)."""
        order = dict(
            client_id=client_id, symbol=symbol or self.symbol, side=side, quantity=quantity
        )
        return self.runner.act([dict(kind='submit_market', order=order)])

    def cancel(self, client_id: str) -> DecisionRecord:
        return self.runner.act([dict(kind='cancel', client_id=client_id)])

    def handoff(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ContractError(
            'handoff uses controlled mode; live authority is fixed for this experiment'
        )


class LivePolicyDecisionRunner(DecisionRunner):
    """Background Human/Rule runner: a pipeline drives the market at 1x on its own threads.

    Owns everything passed in: the controlled ``core`` runner (financial session and
    audit), the experiment ``pipeline`` and ``runtime``, the optional Rule ``host`` and
    ``recorder``, and the ``barrier`` that Platform attaches after construction.
    close() releases them in order and is safe to repeat.

    Args:
        core: The Controlled policy runner that commits every decision.
        pipeline: Drives the market and orders owner operations.
        runtime: Schedules decision work for the Rule child.
        owner: Owner-side observation capture and command handling.
        host: Rule child process host (AgentProcessHost), or None for Human. Typed
            ``Any`` because this package may not import the agents package.
        recorder: Experiment log, closed with the runner.
    """

    background = True

    def __init__(
        self,
        core: PolicyDecisionRunner,
        pipeline: ExperimentSession,
        runtime: ExperimentRuntimeHandle,
        owner: PolicyOwner,
        host: Any = None,
        recorder: Optional[ExperimentLog] = None,
    ) -> None:
        self.core, self.pipeline, self.runtime, self.owner, self.host = (
            core,
            pipeline,
            runtime,
            owner,
            host,
        )
        self.recorder = recorder
        self._closed = False
        self._on_close: Callable[[], None] = lambda: None
        self._controls = LivePolicyControls(self, core.market_snapshot.symbol)
        self.barrier: Optional[ExperimentControls] = None
        self.pipeline.channels.ended.add_done_callback(
            lambda _: host.request_stop() if host else None
        )

    def on_close(self, callback: Callable[[], None]) -> None:
        self._on_close = callback

    @property
    def run_id(self) -> str:
        return self.core.run_id

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def active(self) -> bool:
        return not self.closed and self.pipeline.snapshot()['state'] in (
            'ready',
            'running',
            'paused',
            'pausing',
            'ending',
        )

    @property
    def archive_ready(self) -> bool:
        return not self.active and self.pipeline.channels.ended.done()

    @property
    def policy_controls(self) -> LivePolicyControls:
        return self._controls

    def detached_view(self) -> Optional[Dict[str, Any]]:
        """The owner's last published view plus market data up to the experiment clock."""
        from dataclasses import asdict

        published = self.owner.published
        if published is None:
            return None
        view = copy_json(published)
        cutoff = min(self.pipeline.clock.target_ns(), self.pipeline.reader.manifest['last_ns'])
        reader = self.pipeline.reader
        prefix = reader.prefix(cutoff)
        view['market'] = asdict(reader.snapshot_at(cutoff))
        view['trades'] = [
            asdict(t) for t in reader.history.trades(prefix, cutoff, limit=VIEW_TRADE_LIMIT)
        ]
        view['bars'] = [
            asdict(b) for b in reader.history.bars(prefix, cutoff, limit=VIEW_BAR_LIMIT)
        ]
        return dict(
            view,
            pipeline=self.pipeline.snapshot(),
            runtime=self.runtime.snapshot(),
            control=self.barrier.snapshot() if self.barrier else None,
        )

    def poll(
        self, *, max_events: int = 1, budget_seconds: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        validate_poll(max_events, budget_seconds)
        return self.detached_view()

    def act(self, actions: List[Action], state: Any = None) -> DecisionRecord:
        """Queue a Human response on the owner and wait for its committed result."""
        if self.core.config.mode != 'manual' or self.pipeline.snapshot()['state'] != 'running':
            raise ContractError('manual commands require a running Human experiment')
        return self.pipeline.submit(
            dict(
                request_id='human', operation='manual', response=dict(actions=actions, state=state)
            )
        ).result(CONTROL_ACK_TIMEOUT_SECONDS)['result']

    def play(self, speed: Optional[float] = None) -> Any:
        if speed not in (None, 1) or isinstance(speed, bool):
            raise ContractError(
                'Live experiments support only 1x; use controlled mode for other playback'
            )
        if self.barrier is not None:
            return self.barrier.play()
        state = self.pipeline.snapshot()['state']
        if state == 'ready':
            self.pipeline.start()
        elif state == 'paused':
            self.pipeline.control_and_wait('resume')
        elif state != 'running':
            raise ContractError('experiment ended')
        if self.host:
            self.host.play()
            return None
        return None

    def pause(self) -> Any:
        if self.barrier is not None:
            return self.barrier.pause()
        if self.host:
            self.host.pause()
        self.pipeline.channels.pause_delivery()
        self.pipeline.control_and_wait('pause')
        return None

    def close(self) -> None:
        if self.closed:
            return
        if self.host:
            self.host.request_stop()
        if self.barrier:
            self.barrier.stop()
        else:
            self.pipeline.close()
        if self.host:
            self.host.close()
        if not self.runtime.snapshot()['closed']:
            self.runtime.close()
        self.core.close()
        if self.recorder is not None:
            self.recorder.close()
        self._closed = True
        if self.barrier:
            self.barrier.resources_closed()
        self._on_close()
