"""App-owned desktop resource lifecycle; invoked on the worker thread."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, Tuple, cast

from sim_platform.application.lifecycle import PlatformLifecycle
from sim_platform.domain.models import ContractError
from sim_platform.resources import cleanup_after_error
from sim_platform.research.controller import seed_research

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.application.platform import Platform
    from sim_platform.research.controller import ResearchController
    from .workflows.agents import AgentLibrary
    from .workflows.experiments import ExperimentPreparation
    from .workflows.replay import RecordedReplay


class DesktopRuntime:
    """The Platform resources of one desktop worker, opened and closed on its thread.

    The worker creates it; initialize() opens the research controller, background job,
    batch, dataset library and workflows. ``session`` and the others stay None until
    then. close() closes the Platform and everything it owns.
    """

    # Bound by _bind(); declared here for the attributes it sets.
    agents: AgentLibrary
    experiments: ExperimentPreparation
    replay: RecordedReplay

    def __init__(
        self,
        directory: Any,
        library: Optional[Any] = None,
        demo_source: Optional[Tuple[Any, Any]] = None,
    ) -> None:
        self.lifecycle = PlatformLifecycle(directory)
        self.library, self.demo_source = library, demo_source
        # ResearchController, BackgroundJob, ResearchBatch and DatasetLibrary; typed Any
        # because they are None until initialize() and callers use them only afterwards.
        self.session: Any = None
        self.job: Any = None
        self.batch: Any = None
        self.datasets: Any = None
        self._initialized = False

    @property
    def platform(self) -> Platform:
        return self.lifecycle.open()

    def _bind(self, session: Optional[ResearchController] = None) -> None:
        platform = self.platform
        self.session = session or platform.open_research(library=self.library)
        self.job = platform.open_jobs()
        self.batch = platform.open_batch(library=self.library)
        self.datasets = platform.open_datasets(demo_source=self.demo_source)
        from .workflows.agents import AgentLibrary
        from .workflows.experiments import ExperimentPreparation
        from .workflows.replay import RecordedReplay

        self.agents = AgentLibrary(platform)
        self.experiments = ExperimentPreparation(platform, self.library)
        self.replay = RecordedReplay(platform)

    def initialize(self) -> None:
        if self._initialized:
            return
        try:
            self._bind()
            seed_research(cast('ResearchController', self.session), self.demo_source)
            self._initialized = True
        except Exception as exc:
            self.session = None
            cleanup_after_error(exc, self.close)
            raise

    def reset(self) -> None:
        if self.job is not None and self.job.active:
            raise ContractError('finish or cancel background work before resetting')
        try:
            session = self.lifecycle.reset(library=self.library, demo_source=self.demo_source)
        except Exception:
            # Rejection before shutdown leaves the current bindings intact.
            if self.session is not None and self.session.closed:
                try:
                    self._bind()
                except Exception as recovery:
                    cleanup_after_error(recovery, self.close)
                    raise
            raise
        try:
            self._bind(session)
        except Exception as exc:
            cleanup_after_error(exc, self.close)
            raise

    def replace_session(self, controller: ResearchController) -> None:
        """Adopt a prepared Platform controller only after its background job ends."""
        old = self.session
        try:
            # Replacement happens only after initialize() bound a session.
            cast('ResearchController', old).close()
        except Exception:
            controller.close()
            raise
        self.session = controller

    def close(self) -> None:
        self.lifecycle.close()
