"""Connect a prepared Agent to Runtime work without prescribing its internal loop."""

from __future__ import annotations

from inspect import signature
from typing import TYPE_CHECKING, Any, Dict, Optional, Union, cast
from sim_platform.domain.models import ContractError
from sim_platform.models.contracts import ProviderError
from sim_platform.agents.tools.client.collection import built_in_tools
from .agent import TradingAgent
from .client import AgentClient
from .model import ResponsesModel

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.agents.hosting.client import AgentProcessClient
    from sim_platform.sdk.context import AgentContext, AgentFactory, ResidentAgent, ScheduledAgent

DEFAULT_TASK = 'Evaluate current trading opportunities using the available tools. Finish or wait when done; no trade is required.'


def create_agent(context: AgentContext) -> TradingAgent:
    """Default factory; users can supply another factory without inheriting this template."""
    settings = context.settings
    return TradingAgent(
        model=context.model,
        tools=context.tools.values(),
        instructions=settings['initial_prompt'],
        parameters=settings['parameters'],
    )


class ConfiguredAgentProgram:
    """Prepare one Agent before Play, then delegate scheduled tasks or a resident loop.

    factory is a trusted spawn-picklable Python callable, not an import loader.
    Credentials are configured separately after initialization and before Play.
    """

    def __init__(self, factory: Optional[AgentFactory] = None) -> None:
        self._factory: AgentFactory = factory or create_agent
        self._requires_model = factory is None

    def prepare(
        self, transport: AgentProcessClient, configuration: Dict[str, Any]
    ) -> PreparedAgent:
        """Build the Agent with its context and check its run() fits the carrier kind."""
        from sim_platform.sdk.context import AgentContext

        client = AgentClient(transport, configuration, preparing=True)
        context = AgentContext(
            client, ResponsesModel(client), built_in_tools(client), configuration['template']
        )
        agent = self._factory(context)
        resident = configuration.get('integration') == 'python_carrier'
        method = getattr(agent, 'run', None)
        if not callable(method):
            raise ContractError('create_agent(context) must return an Agent with run()')
        try:
            signature(method).bind(*(() if resident else (object(),)))
        except (TypeError, ValueError) as exc:
            raise ContractError(
                'Agent run signature does not match its configured carrier'
            ) from exc
        return PreparedAgent(client, agent, context, resident, self._requires_model)

    def __call__(self, transport: AgentProcessClient, configuration: Dict[str, Any]) -> None:
        """Convenience for trusted in-process tests; the process host prepares before Play."""
        self.prepare(transport, configuration).run()


class PreparedAgent:
    """Own one execution's Agent state; nothing is shared with another experiment.

    Created by ConfiguredAgentProgram.prepare in the Agent child; run() is called
    once after Play and returns when the Agent stops.
    """

    def __init__(
        self,
        client: AgentClient,
        agent: Union[ScheduledAgent, ResidentAgent],
        context: AgentContext,
        resident: bool,
        requires_model: bool,
    ) -> None:
        self.client, self.agent, self.context, self.resident = client, agent, context, resident
        self.requires_model = requires_model

    def run(self) -> None:
        self.client.activate()
        try:
            if self.requires_model:
                self.client.prepare_model()
            # prepare() checked that run() matches the carrier kind.
            if self.resident:
                cast('ResidentAgent', self.agent).run()
            else:
                while True:
                    cast('ScheduledAgent', self.agent).run(self.context.next_task(DEFAULT_TASK))
        except ProviderError as error:
            self.client.fail(error.code)
        except ContractError:
            if not self.client.stopping:
                raise
