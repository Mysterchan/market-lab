"""List saved runs that can be compared and evaluate a chosen set through Platform.

The Desktop Compare runs page groups finished runs by what made their decisions (an
Agent revision, a rule with its settings, a model file, manual trading) and marks
which runs share a market, so only comparable runs are offered together. The
statistics themselves are Platform.evaluation's (docs/user-guide/evaluation.md).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, cast

from sim_platform.domain.models import ContractError
from sim_platform.research.evaluation import comparable_settings, is_agent_run

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.application.platform import Platform
    from sim_platform.research.jobs import Cancellation

# Run statuses whose results are final.
FINISHED = ('completed', 'stopped', 'failed')
SECOND_NS = 1_000_000_000


def _duration(ns: Any) -> str:
    seconds = int(ns) // SECOND_NS
    if seconds % 86_400 == 0:
        return f'{seconds // 86_400}d'
    if seconds % 3600 == 0:
        return f'{seconds // 3600}h'
    return f'{seconds}s'


def method_label(run: Dict[str, Any]) -> Dict[str, str]:
    """('key', 'label', 'kind') of what made a run's decisions, for grouping runs."""
    config = run.get('config') or {}
    agent = config.get('agent') or {}
    if agent:
        definition = agent.get('definition') or {}
        name = definition.get('name') or 'Agent'
        revision = definition.get('revision') or '?'
        turn = agent.get('speed') == 'turn'
        key = f"agent:{definition.get('agent_id')}:{revision}:{'turn' if turn else 'live'}"
        label = f'{name} · revision {revision} · ' + ('turn-based' if turn else 'Live')
        return dict(key=key, label=label, kind='agent')
    policy = (config.get('decision') or {}).get('policy') or {}
    kind = policy.get('kind') or (run.get('decision_maker') or {}).get('kind') or 'unknown'
    parameters = policy.get('parameters') or {}
    key = 'policy:' + json.dumps(
        dict(kind=kind, name=policy.get('name'), parameters=parameters), sort_keys=True
    )
    if kind == 'human':
        label = 'Manual'
    elif kind == 'model':
        model_file = parameters.get('model_file') or policy.get('name') or 'model'
        label = 'Model · ' + Path(str(model_file)).name
    elif kind == 'rule':
        details = []
        if 'hold_ns' in parameters:
            details.append('hold ' + _duration(parameters['hold_ns']))
        if 'quantity' in parameters:
            details.append(f"{parameters['quantity']} shares")
        label = 'Rule · ' + str(policy.get('name') or 'rule')
        if details:
            label += ' · ' + ', '.join(details)
    else:
        label = str(policy.get('name') or kind)
    return dict(key=key, label=label, kind=kind)


def market_key(run: Dict[str, Any]) -> str:
    """A short digest of the settings that must match for runs to be compared."""
    settings = comparable_settings(dict(run=run))
    encoded = json.dumps(settings, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def market_label(run: Dict[str, Any]) -> str:
    source = (run.get('config') or {}).get('research_source') or {}
    paths = source.get('paths') or ([source['path']] if source.get('path') else [])
    names = ', '.join(Path(str(p)).name for p in paths) or 'Unknown recording'
    symbols = ', '.join(run.get('universe') or [])
    return f'{symbols} · {names}' if symbols else names


def candidates(platform: Platform, profile: str) -> Dict[str, Any]:
    """Finished runs of ``profile`` with their group, market and account names."""
    accounts = {a['id']: a['name'] for a in platform.accounts(profile)}
    rows: List[Dict[str, Any]] = []
    for details in platform.runs(profile, include_records=False):
        run = cast(Dict[str, Any], details)
        if run['status'] not in FINISHED:
            continue
        method = method_label(run)
        initial = run.get('initial_snapshot') or {}
        rows.append(
            dict(
                id=run['id'],
                group=method['key'],
                label=method['label'],
                kind=method['kind'],
                agent=is_agent_run(dict(run=run)),
                account=accounts.get(run['account_id'], run['account_id'][:8]),
                status=run['status'],
                time_ns=run.get('time_ns'),
                market=market_key(run),
                market_label=market_label(run),
                starting_cash=initial.get('cash'),
                comparable=not initial.get('positions'),
            )
        )
    return dict(profile=profile, runs=rows)


def evaluate(
    platform: Platform,
    *,
    profile: str,
    entries: Dict[str, List[str]],
    baseline: Optional[str] = None,
    metric: str = 'total_return',
    training_cutoffs: Optional[Dict[str, str]] = None,
    token_prices: Optional[Dict[str, float]] = None,
    cancellation: Optional[Cancellation] = None,
) -> Dict[str, Any]:
    """Run Platform.evaluation on the chosen groups (a background Desktop task)."""
    if cancellation is not None and cancellation.cancelled:
        raise ContractError('Evaluation cancelled')
    cutoffs = {name: date for name, date in (training_cutoffs or {}).items() if date}
    return platform.evaluation(
        profile,
        entries,
        baseline=baseline or None,
        metric=metric,
        training_cutoffs=cutoffs or None,
        token_prices=token_prices or None,
    )
