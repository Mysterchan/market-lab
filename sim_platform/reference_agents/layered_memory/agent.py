"""A trader with layered memory, after FinMem (Yu et al., 2023).

Observations and the trader's own reflections are stored as memories in three layers:
short (news and quotes, fade within days), mid (reflections, fade within weeks) and
deep (lessons, fade within months). Before each decision the most useful memories are
retrieved by recency, relevance and importance and given to the trader; after it, a
reflection is written back. Paper: https://arxiv.org/abs/2311.13743

This is an independent implementation from the paper's description, not the authors'
code. A reflection on each decision happens at the start of the next round, when its outcome
can be seen. Memory lives only in this run and holds only what the Agent saw, so it cannot
leak later data. Parameters: ``risk`` ('averse', 'neutral' or 'seeking', default
'neutral') and ``memories`` (how many to retrieve, default 8). Grant market.quote,
news.list, account.snapshot, the order tools and control.finish.
"""

import math
import re

from sim_platform.sdk import AgentContext, Task, Tool, TradingAgent

NS_PER_DAY = 86_400 * 1_000_000_000
# Layer: half-life in market days.
LAYERS = dict(short=3, mid=30, deep=180)
RISK = dict(
    averse='Protect capital first: trade only on strong, consistent evidence.',
    neutral='Balance return and risk.',
    seeking='Act on promising evidence even when it is uncertain.',
)
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


def words(text):
    return set(re.findall(r'[a-z0-9]+', text.lower()))


class Memory:
    """Scored memories; time is the market time each was formed."""

    def __init__(self):
        self.items = []

    def add(self, text, layer, importance, time_ns):
        self.items.append(dict(text=text, layer=layer, importance=importance, time_ns=time_ns))

    def recall(self, query, now_ns, count):
        """Top memories by recency (per-layer half-life) + relevance + importance."""
        terms = words(query)

        def score(item):
            age = max(0, now_ns - item['time_ns']) / NS_PER_DAY
            recency = math.exp(-math.log(2) * age / LAYERS[item['layer']])
            overlap = words(item['text']) & terms
            relevance = len(overlap) / (len(terms) or 1)
            return recency + relevance + item['importance']

        return sorted(self.items, key=score, reverse=True)[:count]


class Remember(Tool):
    """Lets the reflecting trader store a lesson and end the reflection."""

    name = 'memory.store'
    description = 'Store one lesson from this decision for future decisions.'
    parameters = dict(
        lesson=dict(type='string', description='What to remember, in one or two sentences.'),
        importance=dict(type='number', description='0 (trivial) to 1 (critical).'),
    )

    def run(self, lesson: str, importance: float) -> dict:
        return dict(lesson=lesson[:1000], importance=min(1.0, max(0.0, float(importance))))


class MemoryTrader:
    def __init__(self, context: AgentContext):
        settings = context.settings
        parameters = settings['parameters']
        self.model = context.model
        self.tools = dict(context.tools)
        self.symbol = settings['symbol']
        self.count = int(parameters.get('memories', 8))
        self.memory = Memory()
        self.now_ns = 0
        # The last decision and the memories it used, reviewed at the next round.
        self.previous = None
        style = RISK.get(parameters.get('risk', 'neutral'), RISK['neutral'])
        self.trader = TradingAgent(
            self.model,
            [self.tools[name] for name in TRADER_TOOLS if name in self.tools],
            settings['initial_prompt'] + f'\n\nRisk character: {style}',
            parameters,
            label='trader',
        )

    def _observe(self):
        """Store the latest quote and news as short-term memories."""
        if 'market.quote' in self.tools:
            quote = self.tools['market.quote'].run(symbol=self.symbol)
            cutoff = (quote.get('cutoff') or {}).get('market_position') or {}
            self.now_ns = int(cutoff.get('time_ns', self.now_ns))
            self.memory.add(f'Quote: {quote.get("data")}', 'short', 0.2, self.now_ns)
        if 'news.list' in self.tools:
            news = self.tools['news.list'].run(limit=5).get('data') or {}
            for item in news.get('items', []):
                self.memory.add(
                    f'News: {item["headline"]}', 'short', 0.5, int(item['available_time_ns'])
                )

    def _reflect(self):
        """Review the previous decision now that its outcome is visible; keep a lesson.

        Reflection happens at the start of the next round: once the trader finishes, the
        round has ended and no further model call belongs to it.
        """
        if self.previous is None:
            return
        decision, used = self.previous
        reflection = TradingAgent(
            self.model,
            [Remember()],
            'You review your own trading decisions and keep useful lessons.',
            finish_tools=('memory.store',),
            label='reflection',
        ).run(
            f'Previous decision: {decision}\nMemories it used:\n{used}\n'
            'Compare it with what has happened since and store one lesson.'
        )
        layer = 'deep' if reflection['importance'] >= 0.7 else 'mid'
        self.memory.add(reflection['lesson'], layer, reflection['importance'], self.now_ns)
        if 'journal.write' in self.tools:
            self.tools['journal.write'].run(text=f'Lesson ({layer}): {reflection["lesson"]}')

    def run(self, task: Task):
        self._observe()
        self._reflect()
        recalled = self.memory.recall(task.instruction, self.now_ns, self.count)
        context = '\n'.join(f'- [{m["layer"]}] {m["text"]}' for m in recalled) or '- none yet'
        result = self.trader.run(
            Task(f'{task.instruction}\n\nRelevant memories:\n{context}', task.notification)
        )
        self.previous = (result, context)
        return result


def create_agent(context: AgentContext):
    """Use with a python_strategy definition: the platform calls run(task) on schedule."""
    return MemoryTrader(context)
