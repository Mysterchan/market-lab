"""A small task-oriented trading Agent; scheduling and transport live outside it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Iterable, Optional, Sequence, Tuple, Union

from sim_platform.domain.models import ContractError
from sim_platform.agents.tools.protocol.errors import ToolError
from sim_platform.agents.tools.client.base import PlatformTool
from sim_platform.agents.tools.client.collection import TOOL_TYPES
from .conversation import Conversation
from .task import Task

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.agents.tools.client.base import Tool
    from sim_platform.sdk.context import AgentModel
    from .model import ToolCall


def tool_error(code: str, message: str) -> Dict[str, Any]:
    """A tool result in the platform's error shape, for errors raised before a tool ran."""
    return dict(status='error', error=dict(code=code, message=message))


class TradingAgent:
    """Run a task using a model and callable Tool objects, retaining this run's history.

    Construct this once for an experiment, then call run(task) for each scheduled
    task. Tools own their interfaces; the model adapter owns provider formatting.
    The Agent only performs the model -> tools -> model loop. An ordinary text
    reply does not finish a task: a successful completion Tool must end it.

    ``label`` names the role (for example 'news analyst'); evaluations report model
    calls and tokens per label. ``finish_tools`` names local tools whose call also ends the task, returning that
    tool's result. Use it for a role inside a larger Agent, such as an analyst that ends
    by submitting a report, so the role never needs the platform's finish or wait.
    """

    def __init__(
        self,
        model: AgentModel,
        tools: Iterable[Tool],
        instructions: str,
        parameters: Optional[Dict[str, Any]] = None,
        finish_tools: Iterable[str] = (),
        label: Optional[str] = None,
    ) -> None:
        self.model = model
        tools = tuple(tools)
        self.tools = {tool.function_name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ContractError('agent tools must have distinct names')
        reserved = {name.replace('.', '_') for name in TOOL_TYPES}
        if any(
            tool.function_name in reserved and not isinstance(tool, PlatformTool) for tool in tools
        ):
            raise ContractError('local tools cannot replace platform tool names')
        self.conversation = Conversation(instructions, parameters or {})
        if label is not None and (
            not isinstance(label, str) or not 0 < len(label) <= 64 or not label.isprintable()
        ):
            raise ContractError('label must be printable text of at most 64 characters')
        self.conversation.label = label
        self.finish_tools = frozenset(name.replace('.', '_') for name in finish_tools)
        unknown = self.finish_tools - {
            name for name, tool in self.tools.items() if not isinstance(tool, PlatformTool)
        }
        if unknown:
            raise ContractError('finish_tools must name local tools given to this agent')

    def _execute(self, call: ToolCall) -> Tuple[Any, bool]:
        """Run one tool call; return its result and whether it ended the task."""
        tool = self.tools.get(call.name)
        if tool is None:
            return tool_error('TOOL_UNAVAILABLE', 'tool is not available to this agent'), False
        try:
            result = tool.invoke(call.arguments())
        except ToolError as error:
            return tool_error(error.code, str(error)), False
        # An uncertain transport error propagates; the Agent never retries a trade.
        if call.name in self.finish_tools:
            return result, True
        return (
            result,
            isinstance(tool, PlatformTool) and tool.ends_work and result.get('status') == 'ok',
        )

    def _mixed_completion(self, calls: Sequence[ToolCall]) -> bool:
        """Whether a finish/wait tool was requested together with other calls."""
        return len(calls) > 1 and any(
            call.name in self.finish_tools
            or (
                isinstance(self.tools.get(call.name), PlatformTool)
                and self.tools[call.name].ends_work
            )
            for call in calls
        )

    def run(self, task: Union[str, Task]) -> dict:
        """Perform a task until finish/wait succeeds, returning that completion result.

        Pass a string for a direct task, or Task to include a scheduler notification.
        Context is retained between calls on this Agent. Each tool result is appended
        before asking the model again; zero trades is a valid completed task.
        """
        task = Task(task) if isinstance(task, str) else task
        if not isinstance(task, Task):
            raise ContractError('agent.run requires task text or a Task')
        self.conversation.start_task(task)
        while True:
            calls = self.model.respond(self.conversation, list(self.tools.values()))
            if not calls:
                self.conversation.continue_work()
                continue
            mixed = self._mixed_completion(calls)
            for call in calls:
                if mixed:
                    result = tool_error('INVALID_ARGUMENT', 'finish and wait must be called alone')
                    finished = False
                else:
                    result, finished = self._execute(call)
                self.conversation.tool_result(call, result)
                if finished:
                    return result
