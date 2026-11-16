"""Worker-side task routing; long work uses the Platform-owned background job.

Widgets send detached values. This router never creates a Platform, process,
financial session or Qt widget. Only the worker adopts a prepared controller.
"""

from __future__ import annotations
import logging

from pathlib import Path
from queue import SimpleQueue, Empty
from typing import TYPE_CHECKING, Any, Dict, Optional, cast
from sim_platform.domain.models import ContractError

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.agents.environments.preparation import EnvironmentPreparation
    from .worker import ResearchWorker


def verify_saved_run(directory: Any, run_id: str, **_: Any) -> str:
    """Replay a finished Controlled run into a temporary folder and describe the outcome.

    Platform.replay_run() repeats the recorded decisions against the same prepared data
    and compares every financial result; it raises ContractError on any difference.
    """
    from tempfile import TemporaryDirectory
    from sim_platform import Platform

    with TemporaryDirectory(prefix='verify-run-') as folder:
        result = Platform.replay_run(directory, run_id, Path(folder) / 'replay')
    return (
        f"Verified · {result['decisions']} decisions and {result['fills']} fills "
        'match the saved run'
    )


class DesktopTasks:
    """Long desktop tasks (datasets, Agents, experiment/replay preparation) of one worker.

    The worker owns one; only one task runs at a time, as the runtime's background
    job (or an environment preparation job), and poll() publishes its progress.
    """

    commands = frozenset(
        (
            'add_dataset',
            'locate_dataset',
            'export_sdk',
            'agents',
            'save_agent',
            'prepare_environment',
            'cancel_preparation',
            'prepare_experiment',
            'prepare_replay',
            'verify_run',
            'save_credential',
            'load_credential',
            'evaluation_runs',
            'evaluate',
            'export_evaluation',
        )
    )

    def __init__(self, worker: ResearchWorker) -> None:
        self.worker = worker
        self.kind: Optional[str] = None
        self.profile: Optional[str] = None
        self.environment: Optional[EnvironmentPreparation] = None
        self.progress: SimpleQueue[Dict[str, Any]] = SimpleQueue()
        # The last evaluation report, kept for its export.
        self.evaluation: Optional[Dict[str, Any]] = None

    @property
    def busy(self) -> bool:
        return self.kind is not None

    def cancel(self) -> None:
        if self.environment is not None:
            self.environment.cancel()
        self.worker.runtime.job.cancel()

    def catalog(self, profile: Optional[str]) -> None:
        if profile:
            self.worker.agents_ready.emit(self.worker.runtime.agents.catalog(profile))

    def execute(self, method: str, arguments: Dict[str, Any]) -> None:
        worker, runtime = self.worker, self.worker.runtime
        if method == 'cancel_preparation':
            self.cancel()
            return
        if self.busy or runtime.job.active:
            raise ContractError('Wait for preparation, or cancel it before starting another task')
        self.profile = arguments.get('profile', self.profile)
        if method == 'export_sdk':
            result = runtime.platform.export_agent_sdk(arguments['path'])
            worker.message.emit('Agent SDK exported: ' + cast(str, result['path']))
            return
        if method == 'agents':
            self.catalog(self.profile)
            return
        if method == 'evaluation_runs':
            from .workflows.evaluation import candidates

            worker.evaluated.emit(
                dict(candidates=candidates(runtime.platform, cast(str, self.profile)))
            )
            return
        if method == 'export_evaluation':
            if self.evaluation is None:
                raise ContractError('Evaluate runs before exporting the report')
            from sim_platform.persistence.export_files import atomic_write
            from sim_platform.research.reports import export_report

            worker.message.emit(
                'Exported: '
                + export_report(self.evaluation, arguments['path'], writer=atomic_write)
            )
            return
        if method in ('save_credential', 'load_credential'):
            if method == 'save_credential':
                runtime.platform.set_model_credential(
                    arguments['reference'],
                    arguments['secret'],
                    persistence=arguments['persistence'],
                )
            else:
                # A saved OS credential needs no copying: this worker's Platform reads the
                # system store on demand. Confirm it exists so a missing key fails here,
                # not later inside an Agent run.
                runtime.platform.check_model_credential(arguments['reference'])
            worker.message.emit(
                'Model credential ready. The key is never included in Agent configuration or logs.'
            )
            return
        if method == 'prepare_environment':
            self.environment = runtime.platform.prepare_agent_environment(
                # Desktop task commands always carry the selected profile.
                cast(str, self.profile),
                arguments['agent_id'],
                arguments['revision'],
                allow_download=arguments.get('allow_download', False),
                allow_python_download=arguments.get('allow_python_download', False),
                python_archive=arguments.get('python_archive'),
                wheelhouse=arguments.get('wheelhouse'),
            )
        else:
            if method in ('prepare_experiment', 'prepare_replay') and runtime.session.active:
                raise ContractError('Stop the current experiment before preparing another one')
            if method in ('add_dataset', 'locate_dataset'):
                operation = (
                    runtime.datasets.add if method == 'add_dataset' else runtime.datasets.locate
                )
                options = dict(arguments, progress=self.progress.put)
                options.pop('profile', None)
                if options.get('book'):
                    from sim_platform.desktop.presentation import money

                    # The form gives the spread in dollars; the library stores money units.
                    book = dict(options['book'])
                    book['spread'] = money(str(book['spread']))
                    options['book'] = book
            elif method == 'save_agent':
                operation, options = runtime.agents.save, arguments
            elif method == 'prepare_experiment':
                worker.timer.stop()
                operation, options = runtime.experiments.prepare, dict(
                    arguments=arguments, progress=self.progress.put
                )
            elif method == 'prepare_replay':
                operation, options = runtime.replay.prepare, arguments
            elif method == 'verify_run':
                operation, options = verify_saved_run, dict(
                    directory=runtime.platform.directory, run_id=arguments['run_id']
                )
            elif method == 'evaluate':
                from .workflows.evaluation import evaluate

                operation, options = evaluate, dict(arguments, platform=runtime.platform)
            else:
                raise ContractError('Unknown desktop task')
            runtime.job.start(operation, **options)
        self.kind = method
        worker.task_changed.emit(dict(state='preparing', phase=method))
        worker.job_changed.emit(True)
        worker.job_timer.start()

    def poll(self) -> None:
        worker, runtime = self.worker, self.worker.runtime
        if not self.busy:
            return
        latest = None
        while True:
            try:
                latest = self.progress.get_nowait()
            except Empty:
                break
        if latest:
            worker.task_changed.emit(dict(state='preparing', **latest))
        if self.environment is not None:
            status = self.environment.snapshot()
            worker.task_changed.emit(status)
            if status['state'] == 'preparing':
                return
            self.environment.close()
            self.environment = None
            self.kind = None
            worker.job_timer.stop()
            worker.job_changed.emit(False)
            self.catalog(self.profile)
            if status['state'] == 'failed':
                worker.failed.emit(str(status.get('error') or 'Environment preparation failed'))
            return
        if not runtime.job.future or not runtime.job.future.done():
            return
        kind, self.kind = self.kind, None
        worker.job_timer.stop()
        try:
            result = runtime.job.take_result()
            if kind in ('add_dataset', 'locate_dataset'):
                worker.datasets_ready.emit(result)
                worker.message.emit('Dataset library updated')
            elif kind == 'prepare_experiment':
                if runtime.job.token.cancelled:
                    result.close()
                    raise ContractError('Experiment preparation cancelled')
                runtime.replace_session(result)
                worker.updated.emit(result.view())
                worker.catalogued.emit(result.catalog(self.profile))
            elif kind == 'save_agent':
                self.catalog(self.profile)
                worker.agent_saved.emit(result)
            elif kind == 'verify_run':
                worker.message.emit(result)
            elif kind == 'evaluate':
                self.evaluation = result
                worker.evaluated.emit(dict(report=result))
            else:
                worker.replay_ready.emit(result)
                worker.catalogued.emit(runtime.session.catalog(self.profile))
            worker.task_changed.emit(dict(state='ready', phase=kind))
            if kind != 'verify_run':
                worker.message.emit(
                    'Ready · press Play to start.'
                    if kind == 'prepare_experiment'
                    else 'Task complete'
                )
        except Exception as exc:
            _log.warning('Handled failure in poll', exc_info=True)
            worker.task_changed.emit(dict(state='failed', phase=kind, error=str(exc)))
            worker.failed.emit(str(exc))
        finally:
            worker.job_changed.emit(False)
