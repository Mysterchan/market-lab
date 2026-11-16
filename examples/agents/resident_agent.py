"""Replace the template entirely while retaining Runtime work and platform tools."""

from sim_platform.sdk import AgentContext


class QuoteWatcher:
    def __init__(self, context: AgentContext):
        self.context = context
        self.quote = context.tool('market.quote')
        self.finish = context.tool('control.finish')
        self.symbol = context.settings['symbol']
        self.observations = []

    def run(self):
        while not self.context.stopping:
            self.context.next_task('Observe the market; trading is optional.')
            self.observations.append(self.quote.run(symbol=self.symbol))
            self.finish.run()


def create_agent(context: AgentContext):
    """Use with a python_carrier definition: the returned object owns run()."""
    return QuoteWatcher(context)
