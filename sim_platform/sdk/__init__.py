"""Public Python interface for defining Agents and their local tools.

Export create_agent(context) from your own module. Return an object with run(task)
for scheduled work, or run() for a resident carrier. Platform owns the experiment
and supplies context inside the Agent process; no internal imports are needed.
"""

from .context import AgentContext, AgentFactory, ScheduledAgent, ResidentAgent
from sim_platform.agents.carrier.agent import TradingAgent
from sim_platform.agents.carrier.task import Task
from sim_platform.agents.tools.client.base import Tool
from sim_platform.agents.tools.protocol.errors import ToolError

__all__ = [
    'AgentContext',
    'AgentFactory',
    'ScheduledAgent',
    'ResidentAgent',
    'TradingAgent',
    'Task',
    'Tool',
    'ToolError',
]
