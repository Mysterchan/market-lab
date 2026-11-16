"""The capabilities supplied to a user Agent factory inside its child process."""

from typing import Any, Callable, Iterable, Protocol, Sequence, TypedDict, Union, TYPE_CHECKING
from types import MappingProxyType
from sim_platform.domain.json_values import copy_json
from sim_platform.agents.carrier.task import Task
from sim_platform.agents.tools.protocol.errors import ToolError
from sim_platform.agents.tools.client.base import PlatformTool, Tool

if TYPE_CHECKING:
    from sim_platform.agents.carrier.model import ToolCall


class AgentSettings(TypedDict):
    """Detached per-run configuration, without credential values or App objects."""

    initial_prompt: str
    parameters: dict
    granted_tools: list
    limits: dict
    symbol: str


class TaskClient(Protocol):
    """The Agent process's work client: waits for tasks and reports Stop."""

    @property
    def stopping(self) -> bool: ...

    def next_task(self, instruction: str) -> Task: ...


class AgentModel(Protocol):
    """A model adapter that turns a conversation and tools into tool calls."""

    def respond(self, conversation: Any, tools: Sequence[Tool]) -> Sequence['ToolCall']:
        """Return tool calls; network IO is performed in the Agent process."""
        ...


class ScheduledAgent(Protocol):
    """An Agent the platform calls once per scheduled task."""

    def run(self, task: Task) -> Any:
        """Perform one scheduled task; use a platform finish/wait tool to end it."""
        ...


class ResidentAgent(Protocol):
    """An Agent that runs its own loop and asks for tasks itself."""

    def run(self) -> None:
        """Own the loop, obtaining work with context.next_task() before using capabilities."""
        ...


class AgentContext:
    """Configure an Agent without exposing App objects, database or market readers.

    model is a lazy model adapter. tools maps granted platform names to remote
    Tool objects. Add your own Tool instances to the Agent's tool list; they run
    locally and are never added to the platform's operation registry.
    """

    def __init__(
        self,
        client: TaskClient,
        model: AgentModel,
        tools: Iterable[PlatformTool],
        settings: AgentSettings,
    ) -> None:
        self._client = client
        self.model = model
        self.tools = MappingProxyType({tool.name: tool for tool in tools})
        self._settings = copy_json(settings)

    @property
    def settings(self) -> AgentSettings:
        """Return a detached copy of prompt, Agent parameters and granted bounds."""
        return copy_json(self._settings)

    def tool(self, name: str) -> PlatformTool:
        """Find a granted platform proxy; unknown or ungranted names fail clearly."""
        try:
            return self.tools[name]
        except KeyError as exc:
            raise ToolError(
                'TOOL_UNAVAILABLE', 'platform tool is not granted or provided: ' + name
            ) from exc

    def next_task(self, instruction: str) -> Task:
        """Wait for Runtime work; valid only after Play, including for resident Agents."""
        return self._client.next_task(instruction)

    @property
    def stopping(self) -> bool:
        return self._client.stopping


AgentFactory = Callable[[AgentContext], Union[ScheduledAgent, ResidentAgent]]
