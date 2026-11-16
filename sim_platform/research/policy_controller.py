"""Human/rule research setup and compatibility controls over PolicyDecisionRunner."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Sequence, Tuple, Union, cast

from sim_platform.domain.models import ContractError
from sim_platform.domain.json_values import copy_json
from sim_platform.decisions import DecisionConfig
from sim_platform.decisions.policy.models import DEFAULT_HOLD_NS, DEFAULT_RULE_QUANTITY
from sim_platform.execution import ExecutionConfig
from sim_platform.resources import cleanup_after_error
from .controller import ResearchController, policy_for
from .configuration import policy_settings

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.experiments.scheduling.clock import SharedExperimentClock
    from sim_platform.decisions.policy.handoffs import HandoffCondition
    from sim_platform.decisions.policy.contracts import Policy
    from sim_platform.decisions.policy.live.runner import LivePolicyDecisionRunner
    from sim_platform.decisions.policy.runner import PolicyDecisionRunner
    from sim_platform.market.native import PathInput
    from sim_platform.market.prepared.session import PreparedSession
    from .reports import ExportWriter
    from .views import SessionView

    # Creates a runner from (profile, run_id, maker_id, market, policy).
    RunnerFactory = Callable[[str, str, str, PreparedSession, Policy], PolicyDecisionRunner]
    LiveRunnerFactory = Callable[[str, str, str, PreparedSession, Policy], LivePolicyDecisionRunner]


class PolicyResearchController(ResearchController):
    """ResearchController for Human and Rule policies in controlled or live mode.

    Created by Platform.open_research for 'human', 'rule', 'hold' or 'model'; it creates the
    run and picks the runner factory for the chosen execution mode. Ownership and
    closing are those of ResearchController.

    Args:
        runner_factory: Creates the controlled-mode PolicyDecisionRunner.
        live_runner_factory: Creates the live-mode LivePolicyDecisionRunner.
        (The other arguments are those of ResearchController.)
    """

    def __init__(
        self,
        operations: Any,
        *,
        market_factory: Callable[[str], PreparedSession],
        runner_factory: RunnerFactory,
        live_runner_factory: LiveRunnerFactory,
        files: Any,
        export_writer: ExportWriter,
    ) -> None:
        super().__init__(
            operations, market_factory=market_factory, files=files, export_writer=export_writer
        )
        self._runner_factories: Dict[str, Union[RunnerFactory, LiveRunnerFactory]] = dict(
            controlled=runner_factory, live=live_runner_factory
        )

    def _policy(
        self,
        policy: str,
        quantity: int,
        hold_ns: int,
        model: Optional[Dict[str, Any]],
        execution_mode: str,
        allow_short: bool = False,
    ) -> Tuple[Policy, DecisionConfig]:
        """Build the selected policy and its decision settings."""
        if policy != 'model':
            if model is not None:
                raise ContractError('model settings need policy=model')
            selected = policy_for(policy, quantity, hold_ns)
            return selected, DecisionConfig(mode='manual' if policy == 'human' else 'automatic')
        if model is None:
            raise ContractError('the model method needs model settings')
        if execution_mode != 'controlled':
            raise ContractError('the model method runs in Controlled mode only')
        from sim_platform.decisions.policy.model_file import ModelFilePolicy

        assert self.market is not None
        selected = ModelFilePolicy(
            model['model_file'],
            self.market.calendar,
            lookback=model['lookback_bars'],
            parameters=model['model_parameters'],
            python=model['model_python'],
            allow_short=allow_short,
        )
        timeout_ms = max(1, int(model['model_timeout_seconds'] * 1000))
        return selected, DecisionConfig(
            mode='automatic', triggers=('market',), timeout_ms=timeout_ms
        )

    def start(
        self,
        profile: str,
        account: str,
        policy: str = 'human',
        quantity: int = DEFAULT_RULE_QUANTITY,
        hold_ns: int = DEFAULT_HOLD_NS,
        fee_per_share: int = 0,
        latency_ns: int = 0,
        execution_config: Optional[ExecutionConfig] = None,
        *,
        execution_mode: str,
        model: Optional[Dict[str, Any]] = None,
        allow_short: bool = False,
    ) -> SessionView:
        """Create a run on the prepared market and bind the runner for ``execution_mode``.

        ``model`` holds the model method's settings (research.configuration.MODEL_DEFAULTS)
        and is required exactly when ``policy`` is 'model'. ``allow_short`` lets sales go
        beyond the shares held under the margin rule in docs/reference/orders.md.
        """
        self._check_open()
        if self._active():
            raise ContractError('a simulation is already active')
        if self.runner is not None:
            raise ContractError('load a fresh market before starting another experiment')
        prepared = bool(
            self.market
            and self._prepared_origin
            == dict(sequence=self.market.snapshot.sequence, time_ns=self.market.time_ns)
            and self.market.status in ('ready', 'paused')
        )
        if self.market is None or (
            not prepared and (self.market.status != 'ready' or self.market.time_ns != 0)
        ):
            raise ContractError('load fresh market data before starting a simulation')
        selected, config = self._policy(
            policy, quantity, hold_ns, model, execution_mode, allow_short
        )
        if execution_config is not None and (fee_per_share or latency_ns):
            raise ContractError('supply execution_config or legacy fee/latency arguments, not both')
        execution = execution_config
        if execution is None:
            execution = ExecutionConfig(fee_per_share=fee_per_share, latency_ns=latency_ns)
        if not isinstance(execution, ExecutionConfig):
            raise ContractError('expected ExecutionConfig')
        from sim_platform.domain.experiment import validate_execution_mode

        mode = validate_execution_mode(execution_mode)
        self._current_source['execution_mode'] = mode
        maker, run = self.app.create_managed_decision_run(
            profile,
            account,
            list(getattr(self.market, 'universe', ())) or self.market.snapshot.symbol,
            selected,
            config,
            execution,
            source=self.source,
            allow_short=allow_short,
        )
        self._execution_mode = mode
        self.profile, self.run_id = profile, run
        try:
            self.runner = self._runner_factories[mode](profile, run, maker, self.market, selected)
        except Exception as exc:

            def stop_failed_start() -> None:
                if self.app.run(profile, run, include_records=False)['status'] == 'running':
                    self.app.execution(
                        'finish', profile, run, 'stopped', 0, 'failed start', write=True
                    )

            cleanup_after_error(exc, stop_failed_start)
            raise
        return self.view()

    def start_from_beginning(self, *, execution_mode: str, **arguments: Any) -> SessionView:
        """Prepare the selected source again from its start point, then start()."""
        from sim_platform.domain.experiment import validate_execution_mode

        validate_execution_mode(execution_mode)
        self._check_open()
        if self._active() or self.viewed_run or not self.source or not self.market:
            raise ContractError('load research data before starting a new simulation')
        source = copy_json(self.source)
        symbol = self.market.snapshot.symbol
        self.begin_prepare(
            source['path'],
            symbol,
            source['news_path'],
            source['allow_implicit_eof'],
            source.get('start_mode', 'beginning'),
            source.get('start_time_ns'),
            book=source.get('book'),
            start_date=source.get('start_date'),
        )
        self.wait_for_preparation()
        return self.start(execution_mode=execution_mode, **arguments)

    def order(
        self,
        client_id: str,
        side: str,
        quantity: int,
        limit_price: Optional[int] = None,
        order_type: str = 'limit',
        symbol: Optional[str] = None,
    ) -> SessionView:
        """Desktop/CLI compatibility; order semantics belong to policy controls.

        ``order_type`` 'market' sends a market order and takes no ``limit_price``.
        ``symbol`` chooses one symbol of an experiment on several symbols.
        """
        options = dict(symbol=symbol) if symbol else {}
        if order_type == 'market':
            self.policy_controls.market_order(client_id, side, quantity, **options)
        elif order_type == 'limit' and limit_price is not None:
            self.policy_controls.order(client_id, side, quantity, limit_price, **options)
        else:
            raise ContractError('a limit order needs limit_price; order_type is limit or market')
        return self.view()

    def cancel(self, client_id: str) -> SessionView:
        self.policy_controls.cancel(client_id)
        return self.view()

    def handoff(
        self,
        policy: str,
        quantity: int = DEFAULT_RULE_QUANTITY,
        hold_ns: int = DEFAULT_HOLD_NS,
        state_mode: str = 'reset',
        condition: Optional[HandoffCondition] = None,
    ) -> SessionView:
        # The rule and position-management methods trade one symbol; an experiment on
        # several symbols can only hand its decisions back to Manual trading.
        if len(getattr(self.market, 'universe', ())) > 1 and policy != 'human':
            raise ContractError('an experiment on several symbols can only switch to Manual')
        self.policy_controls.handoff(policy, quantity, hold_ns, state_mode, condition)
        return self.view()

    def create_experiment(
        self,
        profile: str,
        account: str,
        *,
        source: Union[PathInput, Sequence[PathInput]],
        method: str = 'human',
        settings: Optional[Dict[str, Any]] = None,
        agent_id: Optional[str] = None,
        revision: Optional[int] = None,
        execution_config: Optional[ExecutionConfig] = None,
        log_path: Optional[PathInput] = None,
        clock: Optional[SharedExperimentClock] = None,
        execution_mode: str,
    ) -> SessionView:
        self._check_open()
        if self.runner is not None or self.viewed_run:
            raise ContractError('create the experiment in a fresh controller')
        if (
            agent_id is not None
            or revision is not None
            or log_path is not None
            or clock is not None
        ):
            raise ContractError('agent options require an AgentResearchController')
        from sim_platform.domain.experiment import validate_execution_mode

        validate_execution_mode(execution_mode)
        options = policy_settings(method, settings)
        paths = list(source) if isinstance(source, (list, tuple)) else None
        self.begin_prepare(
            cast('PathInput', paths[0] if paths else source),
            options['symbol'],
            options['news'],
            options['allow_implicit_eof'],
            options['start_mode'],
            options['start_time_ns'],
            book=options['book'],
            start_date=options['start_date'],
            symbols=options['symbols'],
            paths=paths if paths and len(paths) > 1 else None,
        )
        self.wait_for_preparation()
        return self.start(
            profile,
            account,
            method,
            options['quantity'],
            options['hold_ns'],
            execution_config=execution_config,
            execution_mode=execution_mode,
            model=options.get('model'),
            allow_short=options['allow_short'],
        )
