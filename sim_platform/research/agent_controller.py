"""Agent research setup; background ownership remains in AgentDecisionRunner/pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from sim_platform.domain.models import ContractError
from sim_platform.domain.json_values import copy_json
from .controller import ResearchController

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.experiments.scheduling.clock import SharedExperimentClock
    from concurrent.futures import Future

    from sim_platform.decisions.agent.runner import AgentDecisionRunner
    from sim_platform.execution import ExecutionConfig
    from sim_platform.market.native import PathInput
    from sim_platform.market.prepared.session import PreparedSession
    from .controller import ResearchRunner
    from .reports import ExportWriter
    from .views import SessionView

    # Platform._new_agent_run and Platform._new_agent_experiment_run, bound by Platform.
    AgentRunFactory = Callable[..., AgentDecisionRunner]

# Validated Agent experiment settings from agents.configuration: includes 'symbol',
# 'start_ns' and 'source_manifest_ref' among the Agent's other choices.
AgentExperimentSettings = Dict[str, Any]


class AgentResearchController(ResearchController):
    """ResearchController for saved Agents; the runner runs the Agent in the background.

    Created by Platform.open_research(method='agent'). It prepares the source at the
    Agent's start time and binds one AgentDecisionRunner. Ownership and closing are
    those of ResearchController.

    Args:
        agent_factory: Creates a runner from a saved Agent experiment draft.
        agent_experiment_factory: Saves the Agent choices and run, then creates a runner.
        (The other arguments are those of ResearchController.)
    """

    def __init__(
        self,
        operations: Any,
        *,
        market_factory: Callable[[str], PreparedSession],
        agent_factory: Optional[AgentRunFactory],
        agent_experiment_factory: Optional[AgentRunFactory],
        files: Any,
        export_writer: ExportWriter,
    ) -> None:
        super().__init__(
            operations, market_factory=market_factory, files=files, export_writer=export_writer
        )
        self._agent_factory = agent_factory
        self._agent_experiment_factory = agent_experiment_factory

    @property
    def agent_run(self) -> Optional[ResearchRunner]:
        """Compatibility view of the agent subclass, never a second stored owner."""
        return self.runner if self.runner is not None and self.runner.background else None

    def start_agent(
        self,
        profile: str,
        experiment_id: str,
        *,
        source_manifest_ref: str,
        log_path: Optional[PathInput],
        clock: Optional[SharedExperimentClock] = None,
        execution_mode: str,
    ) -> SessionView:
        """Bind a prepared agent experiment; play() launches its background 1x owner.

        Source ref is an explicit caller-declared binding, not a resolved external
        catalog/provider support profile. The caller supplies a trusted prepared source.
        """
        from sim_platform.domain.experiment import validate_execution_mode

        mode = validate_execution_mode(execution_mode)
        self._check_open()
        if (
            self._agent_factory is None
            or self._active()
            or self.runner is not None
            or self.viewed_run
            or not self.source
            or not self.market
            or not self._prepared_origin
            or self.market.status not in ('ready', 'paused')
        ):
            raise ContractError('prepare a fresh market before starting an agent experiment')
        if self._prepared_origin != dict(
            sequence=self.market.snapshot.sequence, time_ns=self.market.time_ns
        ):
            raise ContractError('market changed after preparation')
        source = dict(
            copy_json(self.source),
            symbol=self.market.snapshot.symbol,
            source_manifest_ref=source_manifest_ref,
        )
        agent_run = self._agent_factory(
            profile,
            experiment_id,
            self.market,
            source=source,
            log_path=log_path,
            clock=clock,
            execution_mode=execution_mode,
        )
        self._execution_mode = mode
        self.runner = agent_run
        self.profile, self.run_id = profile, agent_run.run_id
        return self.view()

    def _prepare_agent_source(self, path: PathInput, settings: AgentExperimentSettings) -> None:
        """Prepare the complete source prefix without consuming the next future frame."""
        start = int(settings['start_ns'])
        symbol = settings['symbol']
        # Several symbols may come with one source per symbol.
        sources = list(path) if isinstance(path, (list, tuple)) else None
        path = sources[0] if sources else path
        selected = self.source or {}
        same_source = selected.get('path') == str(Path(path).expanduser().resolve())
        news = selected.get('news_path') if same_source else None
        implicit_eof = selected.get('allow_implicit_eof', False) if same_source else False
        # A price-bar source selected with chosen spread and depth keeps them.
        book = selected.get('book') if same_source else None
        reusable = same_source and self.can_reuse_preparation(
            path,
            symbol,
            news,
            implicit_eof,
            selected.get('start_mode', 'beginning'),
            selected.get('start_time_ns'),
            book,
            selected.get('start_date'),
        )
        symbols = settings.get('symbols')
        if symbols:
            # Several symbols: one source per symbol, or one ITCH file for all of them.
            # The news file chosen with the first symbol's source serves every symbol.
            self.begin_prepare(
                path, symbol, news, implicit_eof, 'custom', start, symbols=symbols, paths=sources
            )
            self.wait_for_preparation()
        elif not reusable or self._loaded_market.time_ns != start:
            self.begin_prepare(path, symbol, news, implicit_eof, 'custom', start, book=book)
            self.wait_for_preparation()
        self._current_source.update(
            symbol=settings['symbol'],
            source_manifest_ref=settings['source_manifest_ref'],
            experiment_start_ns=start,
        )

    def create_agent_experiment(
        self,
        profile: str,
        account: str,
        agent_id: str,
        revision: int,
        *,
        source: PathInput,
        settings: Optional[Dict[str, Any]],
        execution_config: Optional[ExecutionConfig] = None,
        log_path: Optional[PathInput] = None,
        clock: Optional[SharedExperimentClock] = None,
        execution_mode: str,
    ) -> SessionView:
        """Internal orchestration for Platform.open_agent_experiment, no draft ID required."""
        from sim_platform.agents.configuration import validate_experiment_settings

        self._check_open()
        if self._agent_experiment_factory is None or self.runner is not None or self.viewed_run:
            raise ContractError('create the agent experiment in a fresh controller')
        from sim_platform.domain.experiment import validate_execution_mode

        mode = validate_execution_mode(execution_mode)
        settings = validate_experiment_settings(settings)
        self._prepare_agent_source(source, settings)
        self._current_source['execution_mode'] = mode
        runner = self._agent_experiment_factory(
            profile,
            account,
            agent_id,
            revision,
            self.market,
            settings,
            copy_json(self.source),
            execution_config,
            log_path,
            clock,
            execution_mode=mode,
        )
        self._execution_mode = mode
        self.runner = runner
        self.profile, self.run_id = profile, runner.run_id
        return self.view()

    def request_tool(self, request: Dict[str, Any]) -> Future[Any]:
        """Compatibility shortcut; new callers use tool_controls.request_tool()."""
        return self.tool_controls.request_tool(request)

    def create_experiment(
        self,
        profile: str,
        account: str,
        *,
        source: PathInput,
        method: str = 'agent',
        settings: Optional[Dict[str, Any]] = None,
        agent_id: Optional[str] = None,
        revision: Optional[int] = None,
        execution_config: Optional[ExecutionConfig] = None,
        log_path: Optional[PathInput] = None,
        clock: Optional[SharedExperimentClock] = None,
        execution_mode: str,
    ) -> SessionView:
        if method != 'agent' or agent_id is None or revision is None:
            raise ContractError('select an agent and revision for an AgentResearchController')
        return self.create_agent_experiment(
            profile,
            account,
            agent_id,
            revision,
            source=source,
            settings=settings,
            execution_config=execution_config,
            log_path=log_path,
            clock=clock,
            execution_mode=execution_mode,
        )
