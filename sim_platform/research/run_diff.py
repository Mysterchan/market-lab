"""Run diff: what differs between two runs' settings, prompts, models, data and results.

Comparing two runs answers "why did these results differ?": a changed execution
setting, prompt, model version, input file or code commit. ``diff_runs`` walks both
runs' saved configuration and lineage and lists every differing field, then sets the
result figures side by side.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

# Run configuration branches that are identities or caches, not choices.
IGNORED = frozenset(
    (
        'run_id',
        'agent_experiment_id',
        'initial_account_ref',
        'execution_config_ref',
        'artifact',
        'mtime_ns',
        'recorded_at',
        'inode',
        'device',
        'replay',
    )
)
# Result figures set side by side (from the saved report summary).
FIGURES = (
    'total_return',
    'max_drawdown_ratio',
    'sharpe_ratio',
    'fill_count',
    'fees',
    'realized_pnl',
    'turnover',
)


def differences(before: Any, after: Any, path: str = '') -> List[Dict[str, Any]]:
    """Every leaf where two JSON values differ: ``{'field', 'a', 'b'}``, in path order."""
    if isinstance(before, dict) and isinstance(after, dict):
        changes: List[Dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            if key in IGNORED:
                continue
            field = f'{path}.{key}' if path else key
            changes += differences(before.get(key), after.get(key), field)
        return changes
    if before != after:
        return [dict(field=path, a=before, b=after)]
    return []


def _prompt(config: Mapping[str, Any]) -> Optional[str]:
    return ((config.get('agent') or {}).get('definition') or {}).get('initial_prompt')


def diff_runs(platform: Any, profile: str, a: str, b: str) -> Dict[str, Any]:
    """Differences between runs ``a`` and ``b``, grouped by what they describe."""
    from .reports import report_view

    runs = {
        name: platform.run(profile, run_id, include_records=False)
        for name, run_id in (('a', a), ('b', b))
    }
    lineage = {name: platform.run_lineage(profile, run_id) for name, run_id in (('a', a), ('b', b))}
    reports = {
        name: report_view(platform, profile, run_id) for name, run_id in (('a', a), ('b', b))
    }
    config_a, config_b = runs['a']['config'], runs['b']['config']
    model = {name: (lineage[name].get('agent') or {}).get('model') for name in ('a', 'b')}
    served = {name: (lineage[name].get('agent') or {}).get('served_models') for name in ('a', 'b')}

    def inputs(name: str) -> Dict[str, Any]:
        entries = (lineage[name].get('inputs') or {}).get('inputs') or []
        return {entry['field']: entry.get('sha256') for entry in entries}

    def code(name: str) -> Dict[str, Any]:
        return dict((lineage[name].get('inputs') or {}).get('code') or {})

    summary = {name: reports[name].get('summary') or {} for name in ('a', 'b')}
    return dict(
        a=a,
        b=b,
        execution=differences(config_a.get('execution'), config_b.get('execution')),
        settings=differences(
            {k: v for k, v in config_a.items() if k not in ('execution',)},
            {k: v for k, v in config_b.items() if k not in ('execution',)},
        ),
        prompt=(
            None
            if _prompt(config_a) == _prompt(config_b)
            else dict(a=_prompt(config_a), b=_prompt(config_b))
        ),
        model=differences(model['a'], model['b'])
        + (
            [dict(field='served_models', a=served['a'], b=served['b'])]
            if served['a'] != served['b']
            else []
        ),
        data=differences(inputs('a'), inputs('b')),
        code=differences(code('a'), code('b')),
        results=[
            dict(
                figure=figure,
                a=summary['a'].get(figure),
                b=summary['b'].get(figure),
                difference=(
                    summary['b'][figure] - summary['a'][figure]
                    if isinstance(summary['a'].get(figure), (int, float))
                    and isinstance(summary['b'].get(figure), (int, float))
                    else None
                ),
            )
            for figure in FIGURES
            if figure in summary['a'] or figure in summary['b']
        ],
    )
