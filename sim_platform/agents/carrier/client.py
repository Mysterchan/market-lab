"""Keep work identities and IPC envelopes behind the agent-facing tool/model client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, cast

from sim_platform.agents.tools.protocol.contracts import validate_request
from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError
from .task import Task

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.agents.hosting.client import AgentProcessClient
    from sim_platform.experiments.scheduling.client import RuntimeClient


class AgentClient:
    """Use existing process capabilities without exposing request bookkeeping to tools.

    One client belongs to one agent execution. next_task() binds each Runtime work
    identity before the Agent runs it. Tools send a name and arguments; this client
    supplies the run/work/request IDs, validates the request and sends it once.
    It never retries financial operations or reads platform storage directly.
    """

    def __init__(
        self,
        transport: AgentProcessClient,
        configuration: Dict[str, Any],
        *,
        preparing: bool = False,
    ) -> None:
        self._transport = transport
        self._run_id = configuration['run_id']
        self._settings = copy_json(configuration['template'])
        self._preparing = preparing
        self._work: Optional[Dict[str, Any]] = None
        self._request_number = 0

    @property
    def limits(self) -> Dict[str, Any]:
        return copy_json(self._settings['limits'])

    def allows(self, name: str) -> bool:
        return name == 'environment.describe' or name in self._settings['granted_tools']

    def activate(self) -> None:
        """Open capabilities after process Play; initialization must be side-effect free."""
        self._preparing = False

    def _require_active(self) -> None:
        if self._preparing:
            raise ContractError('AGENT_INITIALIZING: capabilities are unavailable before Play')

    def prepare_model(self) -> None:
        self._require_active()
        self._transport.prepare_model()

    def next_task(self, instruction: str) -> Task:
        """Wait for Runtime, then attach its notification to the task's objective.

        Work IDs stay on this client. A notification explains why the task is being
        run now; it does not replace the objective or provide a current price quote.
        """
        self._require_active()
        work: Dict[str, Any] = copy_json(self._transport.next_work())
        self._work = work
        notification = {k: v for k, v in work.items() if k not in ('work_id', 'epoch')}
        return Task(instruction, notification)

    def _require_work(self) -> Dict[str, Any]:
        self._require_active()
        if self._work is None:
            raise ContractError('receive a task before using agent tools or model')
        return self._work

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and send one tool call under the active task, without automatic retry."""
        work = self._require_work()
        self._request_number += 1
        request = dict(
            run_id=self._run_id,
            work_id=work['work_id'],
            epoch=work['epoch'],
            request_id='template-' + str(self._request_number),
            tool=name,
            arguments=arguments,
        )
        binding = dict(
            self._settings, run_id=self._run_id, work_id=work['work_id'], epoch=work['epoch']
        )
        request = validate_request(request, binding)
        return self._transport.request_tool(request)

    def request_model(
        self,
        payload: Dict[str, Any],
        *,
        validate_response: Callable[[Any], object],
        label: Optional[str] = None,
    ) -> Any:
        """Ask the model; ``label`` names the role whose usage this request counts toward."""
        if label is None:
            return self._transport.request_model(
                self._require_work(), payload, validate_response=validate_response
            )
        return self._transport.request_model(
            self._require_work(), payload, validate_response=validate_response, label=label
        )

    def fail(self, code: str) -> None:
        # The Agent process always has a Runtime work client when it can fail work.
        cast('RuntimeClient', self._transport.work_client).fail(code)
        self._transport.wait_stopped()

    @property
    def stopping(self) -> bool:
        return self._transport.stopping
