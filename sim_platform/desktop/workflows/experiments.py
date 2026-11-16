"""Cancellable preparation of one controller through the common Platform entry."""

from __future__ import annotations

from pathlib import Path
from time import sleep
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Union, cast
from sim_platform.resources import cleanup_after_error
from sim_platform.domain.models import ContractError
from sim_platform.execution import ExecutionConfig
from sim_platform.models.contracts import ModelProfile
from .configuration import agent_settings

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike

    from sim_platform.application.platform import Platform
    from sim_platform.research.agent_controller import AgentResearchController
    from sim_platform.research.controller import ResearchController
    from sim_platform.research.jobs import Cancellation
    from sim_platform.research.policy_controller import PolicyResearchController

# Poll interval while the market prepares.
POLL_SECONDS = 0.005
# Default Agent experiment end (16:00) and model timeout.
DEFAULT_END_NS = 57_600_000_000_000
DAY_NS = 86_400_000_000_000
DEFAULT_TIMEOUT_SECONDS = 30


def desktop_model_settings(args: Dict[str, Any]) -> Dict[str, Any]:
    """The model method's settings from the setup form; parameters arrive as JSON text."""
    import json

    from sim_platform.research.configuration import MODEL_DEFAULTS, model_settings

    try:
        parameters = json.loads(args.get('model_parameters') or '{}')
    except ValueError as exc:
        raise ContractError('Model parameters must be a JSON object') from exc
    return model_settings(
        dict(
            MODEL_DEFAULTS,
            model_file=str(Path(str(args.get('model_file', ''))).expanduser()),
            model_parameters=parameters,
            lookback_bars=int(args.get('lookback_bars', MODEL_DEFAULTS['lookback_bars'])),
            model_python=args.get('model_python') or None,
        )
    )


class ExperimentPreparation:
    """Desktop task: prepare one experiment controller (runs as a background job)."""

    def __init__(
        self, platform: Platform, library: Optional[Union[str, PathLike[str]]] = None
    ) -> None:
        self.platform, self.library = platform, library

    def prepare(
        self,
        *,
        arguments: Dict[str, Any],
        progress: Callable[[Dict[str, Any]], None],
        cancellation: Cancellation,
    ) -> ResearchController:
        """Prepare market data, then create the experiment; cancellable between steps."""
        args = dict(arguments)
        method = args.pop('policy')
        controller = self.platform.open_research(method=method, library=self.library)
        try:
            view = controller.begin_prepare(
                args['path'],
                args['symbol'],
                news=args.get('news'),
                allow_implicit_eof=args.get('allow_implicit_eof', False),
                start_mode=args.get('start_mode', 'first_book'),
                start_time_ns=args.get('start_time_ns'),
                book=args.get('book'),
                start_date=args.get('start_date'),
                # Several symbols: one source per symbol (one CSV each, or one ITCH file).
                symbols=args.get('symbols') or None,
                paths=args.get('paths') or None,
            )
            while (view.get('preparation') or {}).get('status') == 'preparing':
                progress(dict(phase='market', preparation=view['preparation']))
                if cancellation.cancelled:
                    controller.cancel_preparation()
                    raise ContractError('Experiment preparation cancelled')
                view = controller.prepare_tick()
                sleep(POLL_SECONDS)
            if cancellation.cancelled:
                raise ContractError('Experiment preparation cancelled')
            progress(
                dict(phase='initializing_agent' if method == 'agent' else 'creating_experiment')
            )
            execution = ExecutionConfig(
                fee_per_share=args['fee_per_share'], latency_ns=args['latency_ns']
            )
            if method == 'agent':
                saved = cast(
                    Dict[str, Any],
                    self.platform.agent(args['profile'], args['agent_id'], args['revision']),
                )
                start = view['market']['time_ns']
                end = args.get('end_ns', DEFAULT_END_NS)
                market = controller.market
                last = market.reader.manifest['last_ns'] if market is not None else 0
                if last >= DAY_NS:
                    # The form gives a time of day. On a multi-day recording it means
                    # that time on the last day, so the Agent runs through every day.
                    end = last // DAY_NS * DAY_NS + end % DAY_NS
                settings = agent_settings(
                    saved['definition'],
                    args['symbol'],
                    start,
                    end,
                    args.get('timeout_seconds', DEFAULT_TIMEOUT_SECONDS),
                    args.get('agent_pacing', 'live'),
                )
                cast('AgentResearchController', controller).create_experiment(
                    args['profile'],
                    args['account'],
                    source=args['path'],
                    agent_id=saved['agent_id'],
                    revision=saved['revision'],
                    settings=settings,
                    execution_config=execution,
                    execution_mode=args['execution_mode'],
                )
                if 'model.request' in saved['definition']['requires']:
                    progress(dict(phase='configuring_model'))
                    model = saved['definition']['parameters'].get('desktop_model')
                    if not model or not model.get('model'):
                        raise ContractError(
                            'Set a model in the saved Agent before preparing an experiment'
                        )
                    # method 'agent' opened an AgentResearchController.
                    self.platform.configure_experiment_model(
                        cast('AgentResearchController', controller), ModelProfile(**model)
                    )
            else:
                # Human/Rule/Model methods open a PolicyResearchController.
                cast('PolicyResearchController', controller).start(
                    args['profile'],
                    args['account'],
                    policy=method,
                    quantity=args['quantity'],
                    hold_ns=args['hold_ns'],
                    execution_config=execution,
                    execution_mode=args['execution_mode'],
                    model=desktop_model_settings(args) if method == 'model' else None,
                    allow_short=bool(args.get('allow_short', False)),
                )
            if cancellation.cancelled:
                raise ContractError('Experiment preparation cancelled')
            return controller
        except Exception as exc:
            cleanup_after_error(exc, controller.close)
            raise
