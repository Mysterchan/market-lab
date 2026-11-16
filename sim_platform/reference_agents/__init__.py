"""Reference Agents: published trading-agent architectures, ready to select and compare.

Each folder is an ordinary Agent project (``agent.py`` exporting ``create_agent``) that
imports only ``sim_platform.sdk``. They are independent implementations from the papers'
descriptions, so researchers can test those designs under Market Lab's execution rules,
leakage guards and statistics. ``Platform.import_reference_agent()`` saves one as an
Agent revision; after that it is used like any imported Agent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from sim_platform.domain.models import ContractError

ROOT = Path(__file__).resolve().parent

READ_TOOLS = [
    'market.quote',
    'market.bars',
    'news.list',
    'fundamentals.get',
    'documents.search',
    'documents.read',
    'account.snapshot',
    'orders.list',
]
TRADE_TOOLS = [
    'journal.write',
    'orders.submit_limit',
    'orders.submit_market',
    'orders.cancel',
    'control.finish',
    'control.wait',
    'model.request',
]

# Key: the folder name. 'requires' are the platform tools to grant.
CATALOGUE: Dict[str, Dict[str, Any]] = {
    'trading_agents': dict(
        name='Multi-role trading firm (TradingAgents)',
        paper='TradingAgents: Multi-Agents LLM Financial Trading Framework',
        url='https://arxiv.org/abs/2412.20138',
        summary='Technical, news and fundamentals analysts, a bull/bear debate, a risk '
        'manager and a trader. Several model calls per decision.',
        parameters={'debate_rounds': 1},
        requires=READ_TOOLS + TRADE_TOOLS,
    ),
    'layered_memory': dict(
        name='Layered-memory trader (FinMem)',
        paper='FinMem: A Performance-Enhanced LLM Trading Agent with Layered Memory and '
        'Character Design',
        url='https://arxiv.org/abs/2311.13743',
        summary='Short, mid and deep memories ranked by recency, relevance and importance; '
        'a reflection after each decision. Configurable risk character.',
        parameters={'risk': 'neutral', 'memories': 8},
        requires=['market.quote', 'news.list', 'account.snapshot', 'orders.list'] + TRADE_TOOLS,
    ),
    'manager_analyst': dict(
        name='Manager and analysts with belief updates (FinCon)',
        paper='FinCon: A Synthesized LLM Multi-Agent System with Conceptual Verbal '
        'Reinforcement for Enhanced Financial Decision Making',
        url='https://arxiv.org/abs/2407.06567',
        summary='Analysts report to one manager who trades; risk control critiques each '
        'decision and rewrites the investment beliefs.',
        parameters={},
        requires=READ_TOOLS + TRADE_TOOLS,
    ),
}


# Execution settings for running a reference Agent as its paper did ('paper': displayed
# prices, no costs) or under realistic frictions ('strict'). Values are ExecutionConfig
# fields; money in USD 1/10,000 (50 = half a cent a share).
PRESETS: Dict[str, Dict[str, Any]] = {
    'paper': dict(),
    'strict': dict(
        model='displayed-limit-queue-v1',
        latency_ns=50_000_000,
        latency_sigma_milli=500,
        fee_per_share=50,
        impact_bps=25,
        borrow_bps_per_year=300,
    ),
}


def execution_preset(name: str) -> Any:
    """An ExecutionConfig for preset ``name`` ('paper' or 'strict').

    Raises:
        ContractError: If the preset is unknown.
    """
    from sim_platform.execution import ExecutionConfig

    if name not in PRESETS:
        raise ContractError(f'unknown preset {name!r}; choose one of {", ".join(PRESETS)}')
    return ExecutionConfig(**PRESETS[name])


def latest_beliefs(notes: List[Dict[str, Any]]) -> Any:
    """The beliefs the manager_analyst Agent last wrote, from Platform.agent_journal()."""
    prefix = 'Beliefs: '
    found = [n['text'][len(prefix) :] for n in notes if n['text'].startswith(prefix)]
    return found[-1] if found else None


def reference_agents() -> List[Dict[str, Any]]:
    """The catalogue as detached records, each with its ``key`` and source ``path``."""
    return [
        dict(
            entry,
            key=key,
            path=str(ROOT / key),
            parameters=dict(entry['parameters']),
            presets=list(PRESETS),
        )
        for key, entry in CATALOGUE.items()
    ]


def reference_agent(key: str) -> Dict[str, Any]:
    """One catalogue record.

    Raises:
        ContractError: If ``key`` is not in the catalogue.
    """
    for entry in reference_agents():
        if entry['key'] == key:
            return entry
    raise ContractError(f'unknown reference Agent {key!r}; choose one of {", ".join(CATALOGUE)}')
