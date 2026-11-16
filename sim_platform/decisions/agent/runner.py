"""Agent decision runner using a pipeline, independent of research and UI.

The runner owns pipeline/log resources and borrows the prepared market.
An experiment controller owns the runner and market; a standalone runner is a
Platform root. Closing the runner drains the pipeline but does not close market.
The trusted process host owns Agent execution and model I/O. Configured agents use
the built-in template after model setup; trusted programs can replace that template.
"""

from __future__ import annotations
import logging

from threading import RLock
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from sim_platform.market.pacing import validate_poll
from sim_platform.domain.models import ContractError
from sim_platform.resources import cleanup_after_error
from sim_platform.decisions.base import DecisionRunner

from sim_platform.experiments.execution.controls import CONTROL_ACK_TIMEOUT_SECONDS

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # Annotation only.
    from concurrent.futures import Future

    from sim_platform.agents.hosting.host import AgentProcessHost
    from sim_platform.agents.operation_log import AgentOperationLog
    from sim_platform.agents.tools.server.router import AgentTools
    from sim_platform.experiments.execution.controls import ExperimentControls
    from sim_platform.experiments.execution.session import ExperimentSession
    from sim_platform.models.contracts import ModelProfile, ModelProvider

    # Turns the runner's detached snapshot into the display view (AgentPresentation).
    Presentation = Callable[[Dict[str, Any]], Dict[str, Any]]


class AgentDecisionRunner(DecisionRunner):
    """Platform side of an Agent run: tool admission, work, and lifecycle controls.

    Owns the pipeline, tool router, recorder, and (once bound) the Agent process host
    and control barrier; close() releases them. It borrows the prepared market. Platform
    binds the host, presentation, local model and barrier during setup, before Play.

    Args:
        pipeline: Drives the market and orders financial and read requests.
        tools: Validates and dispatches the Agent's tool requests.
        run_id: The run this Agent decides for.
        experiment_id: The experiment that groups this run.
        recorder: The Agent operation log, closed with the runner.
    """

    background = True

    def __init__(
        self,
        pipeline: ExperimentSession,
        tools: AgentTools,
        run_id: str,
        experiment_id: str,
        recorder: AgentOperationLog,
    ) -> None:
        self._pipeline = pipeline
        self._tools = tools
        self._run_id = run_id
        self.experiment_id = experiment_id
        self._closed = False
        self._recorder = recorder
        self._process: Optional[AgentProcessHost] = None
        self._tool_used = False
        self._on_close: Callable[[], None] = lambda: None
        self._control_lock = RLock()
        self._barrier: Optional[ExperimentControls] = None
        self._local_model_bound = False
        self._presentation: Optional[Presentation] = None
        # Turn-based experiments only: advances the clock between the Agent's turns.
        self._pacer: Optional[Any] = None

    def bind_pacer(self, pacer: Any) -> None:
        """Attach the TurnPacer of a turn-based experiment; it starts on the first Play."""
        self._pacer = pacer

    @property
    def process_host(self) -> Optional[AgentProcessHost]:
        return self._process

    def bind_process(self, host: AgentProcessHost) -> None:
        """Platform setup only: attach one owned host before Play or tool use."""
        if (
            self._process is not None
            or self._tool_used
            or self._pipeline.snapshot()['state'] != 'ready'
        ):
            raise ContractError('agent process already bound or runner started')
        self._process = host
        if self._tools.controls is not None:
            self._tools.controls.on_end = lambda: host.request_stop()
        self._pipeline.channels.ended.add_done_callback(lambda _: host.request_stop())

    def on_close(self, callback: Callable[[], None]) -> None:
        self._on_close = callback

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def output_finished(self) -> bool:
        """Whether terminal output has drained (including an output failure)."""
        return self._pipeline.channels.ended.done()

    @property
    def archive_ready(self) -> bool:
        return not self.active and self.output_finished

    @property
    def tool_controls(self) -> AgentDecisionRunner:
        return self

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def pipeline(self) -> ExperimentSession:
        """Lower-level trusted harness access; normal callers use session methods."""
        return self._pipeline

    def _check_open(self) -> None:
        if self._closed:
            raise ContractError('agent session is closed')

    @property
    def active(self) -> bool:
        return not self._closed and self._pipeline.snapshot()['state'] in (
            'ready',
            'running',
            'paused',
            'pausing',
            'ending',
        )

    def snapshot(self) -> Dict[str, Any]:
        """Detached run identity and last published owner status; no market reads."""
        return dict(
            mode='agent',
            run_id=self.run_id,
            experiment_id=self.experiment_id,
            pipeline=self._pipeline.snapshot(),
            agent_process=self._process.snapshot() if self._process is not None else None,
            tools_process=self._tools.snapshot(),
            work=self._tools.controls.snapshot() if self._tools.controls is not None else None,
            control=self._barrier.snapshot() if self._barrier is not None else None,
            pacing='turn' if self._pacer is not None else 'live',
            pacer=self._pacer.snapshot() if self._pacer is not None else None,
        )

    def bind_presentation(self, presentation: Presentation) -> None:
        """Platform injects the read-only display capability during assembly."""
        self._presentation = presentation

    def detached_view(self) -> Dict[str, Any]:
        state = self.snapshot()
        return self._presentation(state) if self._presentation is not None else state

    def poll(
        self, *, max_events: int = 1, budget_seconds: Optional[float] = None
    ) -> Dict[str, Any]:
        validate_poll(max_events, budget_seconds)
        return self.snapshot()

    def _tool_delivered(self, request_id: str, future: Future[Any]) -> None:
        """Release the request's admission slot and hand a successful result to the tools."""
        self._tools.controls.delivered(request_id)
        try:
            result = future.result()['result']
        except Exception:
            _log.warning('Handled failure in _tool_delivered', exc_info=True)
            return
        self._tools.delivered(request_id, result)

    def request_tool(self, request: Dict[str, Any]) -> Future[Any]:
        """Ingress validation/admission; owner rechecks current work before effects."""
        self._check_open()
        request = self._tools.validate(request)
        if self._tools.has_retained_request(request):
            return self._submit_tool(request)
        controls = self._tools.controls
        from sim_platform.experiments.scheduling.host import RuntimeError
        from sim_platform.agents.tools.protocol.contracts import ToolError

        try:
            controls.admit_request(request).result(CONTROL_ACK_TIMEOUT_SECONDS)
        except RuntimeError as exc:
            if exc.code in ('CONTRACT_ERROR', 'STALE_WORK'):
                raise ToolError('STALE_WORK', str(exc)) from exc
            raise
        try:
            future = self._submit_tool(request)
        except Exception:
            controls.delivered(request['request_id'])
            raise
        future.add_done_callback(lambda f: self._tool_delivered(request['request_id'], f))
        self._tool_used = True
        return future

    def _submit_tool(self, request: Dict[str, Any]) -> Future[Any]:
        """Market reads use the read path; every other tool is an ordered operation."""
        from sim_platform.agents.tools.protocol.contracts import MARKET_TOOLS

        if request['tool'] in MARKET_TOOLS:
            return self._pipeline.submit_read(request)
        return self._pipeline.submit(request)

    def _runtime_result(
        self, command: str, data: Optional[Dict[str, Any]] = None
    ) -> Future[Dict[str, Any]]:
        """Submit a Runtime command; the returned future resolves to {'result': value}."""
        from concurrent.futures import Future

        result: Future[Dict[str, Any]] = Future()
        source = self._tools.controls.runtime.submit(command, data)

        def complete(future: Future[Any]) -> None:
            try:
                result.set_result(dict(result=future.result()))
            except Exception as exc:
                _log.warning('Handled failure in complete', exc_info=True)
                result.set_exception(exc)

        source.add_done_callback(complete)
        return result

    def next_work(self) -> Future[Dict[str, Any]]:
        """Subscribe to independently scheduled Runtime work."""
        self._check_open()
        return self._runtime_result('next_work')

    def begin_attempt(self) -> Future[Dict[str, Any]]:
        """Trusted test/control capability; provider IO remains Agent-local."""
        self._check_open()
        identity = self._tools.controls.snapshot()
        return self._runtime_result('begin_attempt', {k: identity[k] for k in ('work_id', 'epoch')})

    def receive_attempt(self, attempt_id: str, **reply: Any) -> bool:
        """Deliver a model attempt's reply to the Runtime; False once the run is inactive."""
        if not self.active:
            return False
        return self._tools.controls.runtime.submit(
            'receive_attempt', dict(attempt_id=attempt_id, **reply)
        ).result(CONTROL_ACK_TIMEOUT_SECONDS)['accepted']

    def agent_exited(self, status: str, reason: Optional[str]) -> None:
        if status == 'failed':
            self._tools.controls.agent_failed(reason or 'AGENT_CRASH')
            self._pipeline.wake()

    @property
    def model_configured(self) -> bool:
        return self._local_model_bound

    def bind_local_model(self, profile: ModelProfile, secret: str, provider: ModelProvider) -> None:
        """Setup the selected Agent host; App owns no model service or payload."""
        self._check_open()
        if (
            self.model_configured
            or self._process is None
            or self._pipeline.snapshot()['state'] != 'ready'
        ):
            raise ContractError('bind one local model before Play')
        self._process.configure_model(profile, secret, provider)
        self._local_model_bound = True
        recorded: Dict[str, Any] = dict(
            provider=profile.provider,
            model=profile.model,
            network_timeout_seconds=profile.network_timeout_seconds,
            max_output_tokens=profile.max_output_tokens,
            temperature=profile.temperature,
        )
        if profile.base_url is not None:
            recorded['base_url'] = profile.base_url
        self._tools.controls.record_notice('model_profile', recorded)

    def bind_barrier(self, barrier: ExperimentControls) -> None:
        self._barrier = barrier

    def _play(self, speed: Optional[float] = None) -> Any:
        self._check_open()
        if speed not in (None, 1) or isinstance(speed, bool):
            raise ContractError('agent experiments currently support only 1x')
        # Suspected bug: Play before Platform calls bind_barrier() raises AttributeError
        # on None instead of a ContractError. Left unchanged (no behaviour changes here).
        result = self._barrier.play()  # type: ignore[union-attr]
        if self._pacer is not None:
            self._pacer.start()
        return result

    def _pause(self) -> Any:
        self._check_open()
        # Suspected bug: same unbound-barrier AttributeError as _play().
        return self._barrier.pause()  # type: ignore[union-attr]

    def play(self, speed: Optional[float] = None) -> None:
        with self._control_lock:
            self._play(speed)

    def pause(self) -> None:
        with self._control_lock:
            self._pause()

    def _release(self) -> None:
        # Retain ownership for retry if the child has not drained; do not let
        # the controller close its market while process cleanup is unconfirmed.
        if self._pacer is not None:
            self._pacer.stop()
        if self._process is not None:
            self._process.close()
        runtime = self._tools.controls.runtime
        if not runtime.snapshot()['closed']:
            runtime.close()
        self._tools.close()  # Retain runner ownership if the tools child cannot close.
        self._recorder.close()
        self._closed = True
        if self._barrier is not None:
            self._barrier.resources_closed()
        self._on_close()

    def close(self) -> None:
        if self._closed:
            return
        if self._process is not None:
            self._process.request_stop()
        try:
            if self._barrier is None:
                self._pipeline.close()  # Roll back a failed, not-yet-bound host setup.
            else:
                self._barrier.stop()
        except Exception as exc:
            if self._pipeline.quiescent:
                cleanup_after_error(exc, self._release)
            raise
        self._release()

    def __enter__(self) -> AgentDecisionRunner:
        self._check_open()
        return self
