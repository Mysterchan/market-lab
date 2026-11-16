"""A multi-role trading firm, after the TradingAgents architecture (Xiao et al., 2024).

Analysts study the market, the news and the fundamentals and each submit a report. A
bull and a bear researcher debate those reports, a risk manager reviews the account,
and a trader decides and places orders. Every role is the same model with its own
instructions and only the tools its job needs. Paper: https://arxiv.org/abs/2412.20138

This is an independent implementation from the paper's description, not the authors'
code. Parameters (Agent settings ``parameters``): ``debate_rounds`` (default 1).
Grant market.quote, market.bars, news.list, fundamentals.get, account.snapshot, the
order tools and control.finish; roles whose tools are not granted are skipped.
"""

from sim_platform.sdk import AgentContext, Task, Tool, TradingAgent

REPORT_WORDS = 200

# Role name: (platform tools it may use, its instructions).
ANALYSTS = {
    'technical analyst': (
        ('market.quote', 'market.bars', 'market.trades', 'market.depth'),
        'Study recent prices, volume and the order book. Describe the trend, momentum '
        'and support or resistance, and what they imply for the next few days.',
    ),
    'news analyst': (
        ('news.list',),
        'Read the latest news. Explain which items matter for the stock and whether '
        'their effect is likely positive, negative or already priced in.',
    ),
    'fundamentals analyst': (
        ('fundamentals.get', 'documents.search', 'documents.read'),
        'First call fundamentals.get without metrics to see what exists, then read the '
        'metrics that matter (revenue, earnings, margins, cash, debt). Search filings for '
        'guidance and risks when documents are available. Judge growth, profitability '
        'and balance-sheet strength from the latest periods.',
    ),
}
TRADER_TOOLS = (
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
    """Ends a role's work with its written report."""

    name = 'report.submit'
    description = 'Submit your finished analysis. Call this once, when you are done.'
    parameters = dict(
        report=dict(type='string', description=f'Your analysis in at most {REPORT_WORDS} words.')
    )

    def run(self, report: str) -> dict:
        return dict(report=report[:4000])


def consult(model, tools, role, brief, question):
    """Ask one role for a report; it may use ``tools`` and ends by submitting it."""
    agent = TradingAgent(
        model,
        list(tools) + [SubmitReport()],
        f'You are the {role} of a trading team. {brief} Be specific and concise.',
        finish_tools=('report.submit',),
        label=role,
    )
    return agent.run(question)['report']


class TradingFirm:
    """Analysts, a bull/bear debate and a risk review before every trading decision."""

    def __init__(self, context: AgentContext):
        settings = context.settings
        self.model = context.model
        self.tools = dict(context.tools)
        self.symbol = settings['symbol']
        self.rounds = int(settings['parameters'].get('debate_rounds', 1))
        self.trader = TradingAgent(
            self.model,
            [self.tools[name] for name in TRADER_TOOLS if name in self.tools],
            settings['initial_prompt']
            + '\n\nYou are the trader. Read the team reports, decide whether to buy, sell or '
            'hold, size the order within the risk manager\'s limits, then finish.',
            settings['parameters'],
            label='trader',
        )

    def _granted(self, names):
        return [self.tools[name] for name in names if name in self.tools]

    def run(self, task: Task):
        question = f'Analyse {self.symbol} for the task: {task.instruction}'
        reports = {}
        for role, (names, brief) in ANALYSTS.items():
            tools = self._granted(names)
            if tools:
                reports[role] = consult(self.model, tools, role, brief, question)
        evidence = '\n\n'.join(f'{role}:\n{text}' for role, text in reports.items())
        debate = []
        for _ in range(max(0, self.rounds)):
            for side in ('bull', 'bear'):
                previous = '\n'.join(debate[-2:])
                debate.append(
                    f'{side}: '
                    + consult(
                        self.model,
                        (),
                        f'{side} researcher',
                        f'Argue the {side} case from the evidence and answer the other side.',
                        f'Evidence:\n{evidence}\n\nDebate so far:\n{previous}',
                    )
                )
        risk = consult(
            self.model,
            self._granted(('account.snapshot',)),
            'risk manager',
            'Check cash, position and concentration. State the largest acceptable order '
            'and any reason not to trade.',
            f'Evidence:\n{evidence}\n\nDebate:\n' + '\n'.join(debate),
        )
        if 'journal.write' in self.tools:
            self.tools['journal.write'].run(text=f'Risk manager: {risk}'[:4000])
        briefing = (
            f'{task.instruction}\n\nTeam reports:\n{evidence}\n\nDebate:\n'
            + '\n'.join(debate)
            + f'\n\nRisk manager:\n{risk}'
        )
        return self.trader.run(Task(briefing, task.notification))


def create_agent(context: AgentContext):
    """Use with a python_strategy definition: the platform calls run(task) on schedule."""
    return TradingFirm(context)
