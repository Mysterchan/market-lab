"""Use the supplied template with one additional tool that runs locally."""

from sim_platform.sdk import AgentContext, Tool, TradingAgent


class PercentageChange(Tool):
    name = 'percentage_change'
    description = 'Calculate percentage change between two positive prices.'
    parameters = {
        'before': {'type': 'number', 'exclusiveMinimum': 0},
        'after': {'type': 'number', 'minimum': 0},
    }

    def run(self, before: float, after: float) -> float:
        if before <= 0 or after < 0:
            raise ValueError('prices must be nonnegative and before must be positive')
        return (after - before) / before * 100


def create_agent(context: AgentContext):
    """Return a new instance for this execution; no IO happens during construction."""
    settings = context.settings
    return TradingAgent(
        model=context.model,
        tools=[*context.tools.values(), PercentageChange()],
        instructions=settings['initial_prompt'],
        parameters=settings['parameters'],
    )
