"""Owned persistent agent process; trusted work injection only, no upload loader.

Construction prepares a spawn child before market Play. A supervisor alone
reaps/terminates it; stop/EOF signals never wait on IPC or run on market owner.
The trusted program injection point supplies decision behavior.
"""

from __future__ import annotations
import logging

from math import isfinite
import multiprocessing
from threading import Thread, RLock
import time
import sys
from typing import TYPE_CHECKING, Any, Callable, Dict, Mapping, Optional, Set

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError
from sim_platform.resources import cleanup_after_error
from sim_platform.agents.hosting.bridge import ToolPipeBridge
from sim_platform.experiments.scheduling.signals import ProcessSignal


from .worker import run_program, idle_program
from .processes import create_process_family
from .lifecycle import AgentStartupError, reap_process_family

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # Annotation only.
    from concurrent.futures import Future
    from multiprocessing.connection import Connection
    from multiprocessing.process import BaseProcess

    from sim_platform.experiments.scheduling.client import RuntimeClient
    from sim_platform.models.contracts import ModelProfile, ModelProvider
    from sim_platform.sdk.context import AgentFactory
    from .protocol import TransportLimits

# Startup errors that may be reported to the caller as they are.
REPORTED_STARTUP_ERRORS = (
    'AGENT_DEPENDENCY_MISSING',
    'AGENT_ENTRYPOINT_INVALID',
    'AGENT_TOOL_UNAVAILABLE',
)
# Grants the built-in template needs before a model may be configured.
TEMPLATE_MODEL_GRANTS = frozenset(('model.request', 'control.finish'))
# Startup and supervisor poll intervals, and default timeouts (seconds).
STARTUP_POLL_SECONDS = 0.01
SUPERVISOR_POLL_SECONDS = 0.02
DEFAULT_STARTUP_TIMEOUT_SECONDS = 5
DEFAULT_SHUTDOWN_GRACE_SECONDS = 0.5
# close() waits this long plus three grace periods for the supervisor.
SUPERVISOR_JOIN_BASE_SECONDS = 3

# Called once when the child exits: on_exit(state, error_text_or_None).
ExitCallback = Callable[[str, Optional[str]], Any]


class AgentProcessHost:
    """Runner-owned host borrowing only request_tool and detached configuration.

    program is trusted platform/test code, never resolved from a user manifest.
    A configured definition uses the built-in template. A supplied trusted program
    can replace it completely. JSON configuration contains no credentials or future
    market source paths. The runner owns the host: construction starts the child and
    waits until it is initialized; a supervisor thread reaps it; close() releases the
    process family, pipes and source workspace.
    """

    # Set in __init__; declared here because the lifecycle methods are defined first.
    _lock: RLock
    _close_lock: RLock
    _closed: bool
    _stop_requested: bool
    _state: str
    _error: Optional[str]
    _exit_reason: Optional[str]
    _exit_notified: bool
    _family_reaped: bool
    _exitcode: Optional[int]
    _cleanup_error: Optional[BaseException]
    _grace: float
    _stop: ProcessSignal
    _running: ProcessSignal
    _ready: ProcessSignal
    _family_ready: ProcessSignal
    _process: BaseProcess
    _family: Any
    _bridge: ToolPipeBridge
    _supervisor: Thread
    _on_exit: ExitCallback
    _model_setup_write: Optional[Connection]
    _source_workspace: Optional[Any]

    def request_stop(self) -> None:
        """Request cooperative stop without blocking the experiment execution owner."""
        with self._lock:
            if self._closed:
                return
            self._stop_requested = True
            if self._state not in ('failed', 'completed', 'stopped'):
                self._state = 'stopping'
            self._stop.set()
            self._running.set()

    def _await_family(self, deadline: float) -> None:
        while not self._family_ready.wait(STARTUP_POLL_SECONDS):
            if not self._process.is_alive():
                raise AgentStartupError('AGENT_PROCESS_OWNERSHIP_FAILED')
            if time.monotonic() >= deadline:
                raise AgentStartupError('AGENT_INITIALIZATION_TIMEOUT')

    def _await_initialization(self, deadline: float, startup: Connection) -> None:
        while not self._ready.wait(STARTUP_POLL_SECONDS):
            if self._stop.is_set() or not self._process.is_alive():
                raise AgentStartupError('AGENT_INITIALIZATION_FAILED')
            if time.monotonic() >= deadline:
                raise AgentStartupError('AGENT_INITIALIZATION_TIMEOUT')
        if not startup.poll():
            raise AgentStartupError('AGENT_INITIALIZATION_FAILED')
        try:
            error = startup.recv()
        except (EOFError, OSError) as exc:
            raise AgentStartupError('AGENT_INITIALIZATION_FAILED') from exc
        if error is not None:
            allowed = REPORTED_STARTUP_ERRORS
            raise AgentStartupError(error if error in allowed else 'AGENT_INITIALIZATION_FAILED')
        with self._lock:
            if self._stop.is_set():
                raise AgentStartupError('AGENT_INITIALIZATION_FAILED')
            self._state = 'ready'

    def _reap(self) -> None:
        if not self._family_reaped:
            reap_process_family(self._process, self._family, self._grace)
            self._family_reaped = True

    def _record_exit(self, requested: bool, natural_exit: Optional[int]) -> None:
        with self._lock:
            self._exitcode = self._process.exitcode
            transport_error = self._bridge.snapshot()['error']
            if transport_error:
                self._state, self._error = 'failed', transport_error
                self._exit_reason = 'transport_error'
            elif not requested and natural_exit:
                self._state = 'failed'
                self._error = 'agent process exited with code ' + str(natural_exit)
                self._exit_reason = 'process_crash'
            elif requested:
                self._state, self._exit_reason = 'stopped', 'stop_requested'
            else:
                self._state, self._exit_reason = 'completed', 'program_returned'

    def _notify_exit(self) -> None:
        if not self._exit_notified:
            self._on_exit(self._state, self._error)
            self._exit_notified = True

    def _supervise(self) -> None:
        deadline = None
        try:
            while self._process.is_alive():
                if self._bridge.snapshot()['error'] is not None:
                    self.request_stop()
                if self._stop.is_set():
                    if deadline is None:
                        deadline = time.monotonic() + self._grace
                    elif time.monotonic() >= deadline:
                        break
                self._process.join(SUPERVISOR_POLL_SECONDS)
            requested = self._stop_requested
            # Capture the original exit before tree cleanup changes a still-live child.
            natural_exit = self._process.exitcode
            self._reap()
            self._record_exit(requested, natural_exit)
        except Exception as exc:
            _log.warning('Handled failure in _supervise', exc_info=True)
            with self._lock:
                self._state, self._error = 'failed', 'AGENT_CLEANUP_FAILED'
                self._exit_reason = 'cleanup_failed'
                self._cleanup_error = exc
        finally:
            self._stop.set()
            try:
                self._notify_exit()
            except Exception as exc:
                _log.warning('Handled failure in _supervise', exc_info=True)
                self._cleanup_error = exc
            try:
                self._bridge.close()
            except Exception as exc:
                _log.warning('Handled failure in _supervise', exc_info=True)
                self._cleanup_error = exc

    def close(self) -> None:
        """Reap the owned process family and IPC; retain ownership if cleanup must retry."""
        with self._close_lock:
            if self._closed:
                return
            self.request_stop()
            if self._supervisor.ident is not None:
                self._supervisor.join(SUPERVISOR_JOIN_BASE_SECONDS + 3 * self._grace)
                if self._supervisor.is_alive():
                    raise ContractError('agent process supervisor did not close')
            else:
                # Partial start: the supervisor never took ownership.
                self._reap()
            # Reattempt cleanup after any supervisor failure, without dropping the family.
            if self._cleanup_error is not None:
                self._reap()
            self._bridge.close()
            if self._supervisor.ident is not None:
                self._notify_exit()
            if self._model_setup_write is not None:
                self._model_setup_write.close()
            self._family.close()
            if self._source_workspace is not None:
                self._source_workspace.close()
            self._exitcode = self._process.exitcode
            self._process.close()
            self._cleanup_error = None
            with self._lock:
                self._closed, self._state = True, 'closed'

    def __init__(
        self,
        request_tool: Callable[[Dict[str, Any]], Future[Any]],
        configuration: Dict[str, Any],
        limits: TransportLimits,
        *,
        program: Optional[Callable[..., Any]] = None,
        agent_factory: Optional[AgentFactory] = None,
        launch: Optional[Mapping[str, Any]] = None,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
        shutdown_grace: float = DEFAULT_SHUTDOWN_GRACE_SECONDS,
        request_work: Optional[Callable[[], Future[Any]]] = None,
        on_exit: Optional[ExitCallback] = None,
        work_client: Optional[RuntimeClient] = None,
        local_model: bool = False,
    ) -> None:
        configuration = copy_json(configuration)
        if type(configuration) is not dict:
            raise ContractError('process requires JSON configuration and trusted program')
        if agent_factory is not None and (not callable(agent_factory) or program is not None):
            raise ContractError('supply one trusted Agent factory, without a raw program')
        self._uses_template = program is None and 'template' in configuration
        if agent_factory is not None and not self._uses_template:
            raise ContractError('Agent factory requires Agent configuration')
        if self._uses_template:
            from sim_platform.agents.carrier.program import ConfiguredAgentProgram

            program = ConfiguredAgentProgram(agent_factory)
        elif program is None:
            program = idle_program
        self._uses_template = self._uses_template and agent_factory is None
        self._template_grants: Set[str] = set(
            configuration.get('template', {}).get('granted_tools', ())
        )
        if not callable(program):
            raise ContractError('process requires JSON configuration and trusted program')
        for value in (startup_timeout, shutdown_grace):
            if type(value) not in (int, float) or not isfinite(value) or value <= 0:
                raise ContractError('process timeouts must be positive and finite')
        self._on_exit = on_exit or (lambda status, reason: None)
        self._lock = RLock()
        self._close_lock = RLock()
        self._exit_notified = False
        self._family_reaped = False
        self._exit_reason = None
        self._stop_requested = False
        self._closed = False
        self._source_workspace = launch.get("workspace") if launch is not None else None
        self._model_configured = False
        self._model_setup_write = None
        self._error = None
        self._state = 'preparing'
        self._grace = shutdown_grace
        self._exitcode = None
        self._pid: Optional[int] = None
        self._cleanup_error = None
        context = multiprocessing.get_context('spawn')
        self._ready, self._stop, self._running = (
            ProcessSignal(context),
            ProcessSignal(context),
            ProcessSignal(context),
        )
        self._family_ready = ProcessSignal(context)
        self._launch = ProcessSignal(context)
        self._family = create_process_family()
        model_setup_read: Optional[Connection] = None
        if local_model:
            model_setup_read, self._model_setup_write = context.Pipe(duplex=False)
        startup_read, startup_write = context.Pipe(duplex=False)
        request_read, request_write = context.Pipe(duplex=False)
        response_read, response_write = context.Pipe(duplex=False)
        self._bridge = ToolPipeBridge(
            request_tool, request_read, response_write, limits, request_work=request_work
        )
        process: Callable[..., BaseProcess] = context.Process
        process_options: Dict[str, Any] = {}
        if launch is not None:
            from .processes.interpreter import InterpreterProcess

            process = InterpreterProcess
            process_options = dict(
                executable=launch["executable"],
                project=launch["project"],
                sandbox=launch.get("sandbox"),
            )
        self._executable = launch["executable"] if launch is not None else sys.executable
        self._process = process(
            **process_options,
            target=run_program,
            name='agent-program',
            args=(
                request_write,
                response_read,
                self._ready,
                self._stop,
                self._running,
                limits,
                program,
                configuration,
                work_client,
                model_setup_read,
                startup_write,
                self._family_ready,
                self._launch,
            ),
        )
        self._supervisor = Thread(target=self._supervise, name='agent-process-supervisor')
        started = time.monotonic()
        try:
            self._process.start()
            self._pid = self._process.pid
            self._family.attach(self._process)
            self._await_family(started + startup_timeout)
            self._family.confirm()
            self._launch.set()
            startup_write.close()
            if model_setup_read is not None:
                model_setup_read.close()
            # Do not retain peer endpoints: process death must wake bridge IO.
            request_write.close()
            response_read.close()
            self._bridge.start()
            self._supervisor.start()
            self._await_initialization(started + startup_timeout, startup_read)
            self._startup_seconds = time.monotonic() - started
        except Exception as exc:
            request_write.close()
            response_read.close()
            cleanup_after_error(exc, self.close)
            raise
        finally:
            if work_client is not None:
                work_client.close()  # Release parent duplicates after success or partial start.
            if model_setup_read is not None:
                model_setup_read.close()
            startup_read.close()
            startup_write.close()

    def configure_model(self, profile: ModelProfile, secret: str, provider: ModelProvider) -> None:
        """Dedicated setup pipe: selected credentials never enter JSON config/logs/argv."""
        with self._lock:
            if self._model_setup_write is None or self._model_configured or self._state != 'ready':
                raise ContractError('configure one Agent-local model before Play')
            if self._uses_template and not TEMPLATE_MODEL_GRANTS <= self._template_grants:
                raise ContractError(
                    'built-in template requires model.request and control.finish grants'
                )
            from sim_platform.models.credentials import validate_secret

            # A local model server may need no key; only that provider may omit one.
            keyless = secret == '' and profile.provider == 'openai-compatible'
            key = '' if keyless else validate_secret(secret)
            self._model_setup_write.send((profile, key, provider))
            self._model_configured = True
            self._model_setup_write.close()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(
                state=self._state,
                executable=self._executable,
                pid=self._pid,
                exitcode=self._exitcode,
                error=self._error,
                startup_seconds=getattr(self, '_startup_seconds', None),
                transport=self._bridge.snapshot(),
                exit_reason=self._exit_reason,
                stop_requested=self._stop_requested,
                cleanup_pending=self._cleanup_error is not None,
                process_family=self._family.name,
                pause_mode='capability_gate',
            )

    def play(self) -> Dict[str, Any]:
        with self._lock:
            if self._stop.is_set() or self._closed:
                # Confirm the unavailable gate without pretending the child
                # resumed. Existing accepted orders retain their lifecycle.
                return dict(participant='decision', phase='unavailable', pid=self._pid)
            self._state = 'running'
            self._running.set()
            return dict(participant='decision', phase='gate_open', pid=self._pid)

    def pause(self) -> Dict[str, Any]:
        with self._lock:
            if not self._stop.is_set():
                self._running.clear()
                self._state = 'paused'
            return dict(participant='decision', phase='gate_closed', pid=self._pid)
