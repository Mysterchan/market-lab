"""A manager with analysts and a self-critiquing risk control, after FinCon (Yu et al., 2024).

Analysts each read one kind of data and report to a manager, who alone trades. After
every decision a risk-control step compares the outcome with the manager's current
investment beliefs and rewrites them in words ("conceptual verbal reinforcement"). The
updated beliefs guide the next decision. The critique of a decision runs at the start of
the next round, when its outcome can be seen. Paper: https://arxiv.org/abs/2407.06567

This is an independent implementation from the paper's description, not the authors'
code. Beliefs persist across tasks within a run and are written to the run's journal;
pass the last ones as the ``beliefs`` parameter of the next run to continue learning
(``sim_platform.reference_agents.latest_beliefs``), as the paper's training episodes do. Grant market.quote,
market.bars, news.list, fundamentals.get, account.snapshot, the order tools and
control.finish.
"""

from sim_platform.sdk import AgentContext, Task, Tool, TradingAgent

# Journal notes starting with this carry the beliefs into the next run (see latest_beliefs).
BELIEFS_PREFIX = 'Beliefs: '

ANALYSTS = {
    'market analyst': (('market.quote', 'market.bars'), 'price trend and volatility'),
    'news analyst': (('news.list',), 'recent news and its likely effect'),
    'fundamentals analyst': (
        ('fundamentals.get', 'documents.search', 'documents.read'),
        'growth, margins, balance sheet and what the filings say',
    ),
}
MANAGER_TOOLS = (
    'market.quote',
    'account.snapshot',
    'orders.list',
    'orders.submit_limit',
    'orders.submit_market',
    'orders.cancel',
    'control.finish',
    'control.wait',
)


class SubmitReport(Tool):
    name = 'report.submit'
    description = 'Submit your finished report to the manager. Call once, when done.'
    parameters = dict(report=dict(type='string', description='At most 150 words.'))

    def run(self, report: str) -> dict:
        return dict(report=report[:3000])


class UpdateBeliefs(Tool):
    name = 'beliefs.update'
    description = 'Replace the investment beliefs with an improved version.'
    parameters = dict(beliefs=dict(type='string', description='At most 150 words.'))

    def run(self, beliefs: str) -> dict:
        return dict(beliefs=beliefs[:3000])


class InvestmentFirm:
    def __init__(self, context: AgentContext):
        settings = context.settings
        self.model = context.model
        self.tools = dict(context.tools)
        self.symbol = settings['symbol']
        # Beliefs a previous run ended with (Platform.agent_journal), or a cautious start.
        self.beliefs = str(
            settings['parameters'].get('beliefs')
            or 'No beliefs yet: start cautiously and learn from outcomes.'
        )
        # The last decision and its reports, critiqued at the next round.
        self.previous = None
        self.manager = TradingAgent(
            self.model,
            [self.tools[name] for name in MANAGER_TOOLS if name in self.tools],
            settings['initial_prompt']
            + '\n\nYou are the portfolio manager. Combine the analyst reports with your '
            'investment beliefs, decide, place any orders and finish.',
            settings['parameters'],
            label='manager',
        )

    def _report(self, role, names, focus, question):
        tools = [self.tools[name] for name in names if name in self.tools]
        if not tools:
            return None
        analyst = TradingAgent(
            self.model,
            tools + [SubmitReport()],
            f'You are the {role}. Report on {focus} to your manager.',
            finish_tools=('report.submit',),
            label=role,
        )
        return analyst.run(question)['report']

    def _critique(self):
        """Risk control reviews the previous decision, now that its outcome shows.

        It runs at the start of the next round, because a round ends when the manager
        finishes. The updated beliefs go to the journal so a later run can resume them.
        """
        if self.previous is None:
            return
        decision, briefing = self.previous
        critique = TradingAgent(
            self.model,
            [UpdateBeliefs()]
            + ([self.tools['account.snapshot']] if 'account.snapshot' in self.tools else []),
            'You are risk control. Compare the previous decision and the account now with '
            'the beliefs. Keep what worked, correct what did not, then update the beliefs.',
            finish_tools=('beliefs.update',),
            label='risk control',
        )
        self.beliefs = critique.run(
            f'Beliefs:\n{self.beliefs}\n\nPrevious decision:\n{decision}\n\n'
            f'Reports it used:\n{briefing}'
        )['beliefs']
        if 'journal.write' in self.tools:
            self.tools['journal.write'].run(text=BELIEFS_PREFIX + self.beliefs)

    def run(self, task: Task):
        self._critique()
        question = f'{self.symbol}: {task.instruction}'
        reports = {
            role: self._report(role, names, focus, question)
            for role, (names, focus) in ANALYSTS.items()
        }
        briefing = '\n\n'.join(f'{role}:\n{text}' for role, text in reports.items() if text)
        result = self.manager.run(
            Task(
                f'{task.instruction}\n\nInvestment beliefs:\n{self.beliefs}\n\n'
                f'Analyst reports:\n{briefing}',
                task.notification,
            )
        )
        self.previous = (result, briefing)
        return result


def create_agent(context: AgentContext):
    """Use with a python_strategy definition: the platform calls run(task) on schedule."""
    return InvestmentFirm(context)
