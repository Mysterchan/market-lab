"""Prepared run barriers with acknowledgements from the actual capability owners.

Runtime and Execution confirm quiescence. Agent/Tools hosts confirm their
activation/delivery gates; this never claims arbitrary Python CPU code is paused.
"""

from __future__ import annotations

from threading import RLock
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Protocol

from sim_platform.domain.models import ContractError
from sim_platform.domain.json_values import copy_json
from sim_platform.resources import cleanup_after_error

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.experiments.execution.session import ExperimentSession
    from sim_platform.experiments.scheduling.host import ExperimentRuntimeHandle

# Internal coordination bounds, independent of user-configured model timeouts.
CONTROL_ACK_TIMEOUT_SECONDS = 5
EXECUTION_DRAIN_TIMEOUT_SECONDS = 5

# A participant's or the Runtime's acknowledgement: 'participant' plus phase details.
Acknowledgement = Dict[str, Any]
# A confirmed control: 'request', 'state', 'acknowledgements' and 'position'.
ControlRecord = Dict[str, Any]


class Participant(Protocol):
    """A host that confirms its own activation gate (the Tools host or a decision host).

    play() and pause() return {'participant': 'tools' or 'decision', ...}.
    """

    def play(self) -> Acknowledgement: ...

    def pause(self) -> Acknowledgement: ...


class DecisionParticipant(Participant, Protocol):
    """The Agent or Rule host, which can also be asked to stop."""

    def request_stop(self) -> None: ...


class ExperimentControls:
    """Play, Pause and Stop for one background experiment, acknowledged by each owner.

    Assembly creates one per run and binds it to the runner, which calls it from the
    caller's thread. It borrows the pipeline, Runtime and hosts; stop() closes the
    pipeline and Runtime, and the runner closes the hosts.

    Args:
        pipeline: The run's ExperimentSession.
        runtime: The run's Runtime process handle.
        decision: The Agent or Rule host, if any.
        tools: The Tools host, if any.
        record: Receives each confirmed control for the experiment log.
    """

    def __init__(
        self,
        pipeline: ExperimentSession,
        runtime: ExperimentRuntimeHandle,
        *,
        decision: Optional[DecisionParticipant] = None,
        tools: Optional[Participant] = None,
        record: Optional[Callable[[ControlRecord], Any]] = None,
    ) -> None:
        self.pipeline, self.runtime = pipeline, runtime
        self.decision, self.tools = decision, tools
        self.record: Callable[[ControlRecord], Any] = record or (lambda value: None)
        self._lock = RLock()
        self.version = 0
        self.state = 'ready'
        self.acknowledgements: List[ControlRecord] = []

    def _request(self, action: str) -> Dict[str, Any]:
        self.version += 1
        return dict(control_id=str(self.version), control_version=self.version, action=action)

    def _runtime(self, request: Dict[str, Any]) -> Acknowledgement:
        return self.runtime.submit('control', request).result(CONTROL_ACK_TIMEOUT_SECONDS)

    def _confirmed(
        self, request: Dict[str, Any], state: str, acks: List[Acknowledgement]
    ) -> ControlRecord:
        value = dict(
            request=request,
            state=state,
            acknowledgements=acks,
            position=self.pipeline.snapshot()['position'],
        )
        self.state = state
        self.acknowledgements = [copy_json(value)]
        self.record(value)
        return value

    def _gates(self, action: str) -> List[Acknowledgement]:
        """Call ``action`` ('play' or 'pause') on each host and check its acknowledgement."""
        acknowledgements = []
        for resource, name in ((self.tools, 'tools'), (self.decision, 'decision')):
            if resource is None:
                continue
            result = getattr(resource, action)()
            if not isinstance(result, dict) or result.get('participant') != name:
                raise ContractError('participant did not acknowledge its capability gate')
            acknowledgements.append(result)
        return acknowledgements

    def _failed_control(self, error: Exception) -> Optional[Dict[str, Any]]:
        """Resolve a concurrent terminal result or retain the original control failure."""
        if self.pipeline.snapshot()['state'] in ('completed', 'stopped', 'failed', 'ending'):
            terminal = self.pipeline.channels.ended.result(CONTROL_ACK_TIMEOUT_SECONDS)
            self.state = 'terminal'
            return dict(state='terminal', participant='execution', terminal=terminal)
        self.state = 'control_failed'
        cleanup_after_error(error, lambda: self.pipeline.control('stop'))
        return None

    def play(self) -> Optional[Dict[str, Any]]:
        """Start or resume; None if already running, a terminal result if the run ended."""
        with self._lock:
            if self.state == 'running':
                return None
            if self.state not in ('ready', 'paused'):
                raise ContractError('experiment cannot start/resume from this state')
            request = self._request('play' if self.state == 'ready' else 'resume')
            try:
                runtime = self._runtime(request)
                if self.state == 'ready':
                    self.pipeline.start()
                    execution = dict(
                        participant='execution',
                        phase='started',
                        position=self.pipeline.snapshot()['position'],
                    )
                else:
                    execution = self.pipeline.control_and_wait('resume')
                gates = self._gates('play')
                return self._confirmed(request, 'running', [runtime, execution, *gates])
            except Exception as error:
                terminal = self._failed_control(error)
                if terminal is not None:
                    return terminal
                raise

    def pause(self) -> Optional[Dict[str, Any]]:
        """Pause; None if not running, a terminal result if the run ended meanwhile."""
        with self._lock:
            if self.state != 'running':
                return None
            request = self._request('pause')
            self.state = 'pausing'
            try:
                # Clock freeze + admission close linearize in the Sequencer.
                applied = self.pipeline.control('pause')
                self.pipeline.channels.pause_delivery()
                gates = self._gates('pause')
                runtime = self._runtime(request)
                execution = applied.result(CONTROL_ACK_TIMEOUT_SECONDS)
                return self._confirmed(request, 'paused', [runtime, execution, *gates])
            except Exception as error:
                terminal = self._failed_control(error)
                if terminal is not None:
                    return terminal
                raise

    def stop(self) -> None:
        with self._lock:
            if self.state in ('terminal', 'resources_closed'):
                return
            self.state = 'stopping'
            request = self._request('stop')
            if self.decision:
                self.decision.request_stop()
            self.pipeline.close()
            self.runtime.close()
            # Terminal stream is already sealed. Keep detached final acks only.
            record, self.record = self.record, lambda value: None
            try:
                self._confirmed(
                    request,
                    'terminal',
                    [
                        dict(
                            participant='execution',
                            phase='terminal',
                            position=self.pipeline.snapshot()['position'],
                        ),
                        dict(
                            participant='runtime',
                            phase='closed',
                            closed=self.runtime.snapshot()['closed'],
                        ),
                    ],
                )
            finally:
                self.record = record

    def snapshot(self) -> Dict[str, Any]:
        return dict(
            state=self.state,
            control_version=self.version,
            acknowledgements=copy_json(self.acknowledgements),
        )

    def resources_closed(self) -> None:
        self.state = 'resources_closed'
