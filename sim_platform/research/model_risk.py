"""Model risk report: one document with a study's assumptions, data, tests and limits.

Built for model validation and review: for each run it states what was assumed
(execution model, latency, costs, short selling), what data and code were used
(lineage with digests), what model answered (provider, versions, usage, budget), what
was checked (lineage verification, memorisation flags, evaluation statistics when
several runs are reported) and the limits that remain. ``render_markdown`` turns the
JSON report into a Markdown document for review or archiving.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

# Report layout version, so archived reports can be read by later tools.
FORMAT = 'market-lab-model-risk-report-v1'

# Limits that hold for every run; see docs/reference/methodology.md.
GENERAL_LIMITS = (
    'Orders take only displayed liquidity and do not change the historical book; large '
    'orders see more liquidity than they would in reality unless impact_bps is set.',
    'Fills record the price paid, not the mid price, so spread cost is not separated '
    'from other costs.',
    'Borrow is unlimited; a short still open at the end of a run is charged borrow only '
    'in the reported costs (borrow_accrued), not in the cash ledger.',
    'Price-bar recordings use a synthetic book built from each bar; their intrabar path '
    'and depth are assumptions, not observations.',
    'Repeated runs on one recording measure a method\'s own randomness, not market '
    'uncertainty; compare several recordings for robustness.',
    'Lineage files are local records, not signatures: anyone with write access to the '
    'data folder could rewrite a record and its seal together.',
)


def _assumptions(execution: Optional[Mapping[str, Any]]) -> List[str]:
    """Plain statements of a run's execution settings."""
    if not execution:
        return ['Execution settings were not recorded for this run.']
    lines = [f'Execution model {execution.get("model")}.']
    if execution.get('model') == 'displayed-limit-v1':
        lines.append('Resting limit orders never fill (conservative for passive strategies).')
    elif execution.get('model') == 'displayed-limit-queue-v1':
        lines.append(
            'Resting orders fill only after the displayed shares ahead of them have traded.'
        )
    latency = execution.get('latency_ns', 0)
    lines.append(
        f'Order latency {latency / 1e6:g} ms'
        + (
            f', jitter up to {execution["latency_jitter_ns"] / 1e6:g} ms'
            if execution.get('latency_jitter_ns')
            else ''
        )
        + (
            f', lognormal sigma {execution["latency_sigma_milli"] / 1000:g}'
            if execution.get('latency_sigma_milli')
            else ''
        )
        + '.'
    )
    lines.append(f'Commission {execution.get("fee_per_share", 0) / 1e4:g} USD per share.')
    lines.append(
        f'Market impact {execution.get("impact_bps", 0)} bps at full displayed depth '
        '(square-root model).'
        if execution.get('impact_bps')
        else 'No market impact model: the book does not react to simulated orders.'
    )
    lines.append(
        f'Borrow cost {execution["borrow_bps_per_year"]} bps per year, charged at cover.'
        if execution.get('borrow_bps_per_year')
        else 'No borrow cost.'
    )
    return lines


def run_section(platform: Any, profile: str, run_id: str) -> Dict[str, Any]:
    """Everything the report states about one run."""
    from .evaluation import agent_model_usage, data_dates, memorisation_flags
    from .reports import report_view

    report = report_view(platform, profile, run_id)
    lineage = platform.run_lineage(profile, run_id)
    proof = platform.verify_run(profile, run_id)
    config = (report.get('run') or {}).get('config') or {}
    inputs = lineage.get('inputs') or {}
    section: Dict[str, Any] = dict(
        run_id=run_id,
        status=(report.get('run') or {}).get('status'),
        universe=(report.get('run') or {}).get('universe'),
        dates=data_dates(report),
        method=(
            'agent'
            if config.get('agent')
            else ((config.get('decision') or {}).get('policy') or {}).get('kind', 'manual')
        ),
        assumptions=_assumptions(config.get('execution')),
        execution=config.get('execution'),
        code=inputs.get('code'),
        data=[
            {k: entry.get(k) for k in ('field', 'path', 'kind', 'bytes', 'sha256', 'error')}
            for entry in inputs.get('inputs') or []
        ],
        summary=report.get('summary'),
        verification=dict(
            unchanged=proof['unchanged'],
            sealed=proof['sealed'],
            code_changed=proof['code_changed'],
            failed=[c['item'] for c in proof['checks'] if not c['match']],
        ),
    )
    agent = config.get('agent')
    if agent:
        archive = platform.agent_log_archive(profile, run_id)
        usage = agent_model_usage(archive) or {}
        section['agent'] = dict(
            agent_id=(agent.get('definition') or {}).get('agent_id'),
            revision=(agent.get('definition') or {}).get('revision'),
            pacing='turn' if agent.get('speed') == 'turn' else 'live',
            granted_tools=agent.get('granted_tools'),
            budget=agent.get('budget'),
            model=(lineage.get('agent') or {}).get('model'),
            served_models=usage.get('served_models'),
            model_calls=usage.get('model_calls'),
            failed_calls=usage.get('failed_calls'),
            input_tokens=usage.get('input_tokens'),
            output_tokens=usage.get('output_tokens'),
            budget_exceeded=usage.get('budget_exceeded'),
        )
        section['flags'] = memorisation_flags(report, None)
    return section


def model_risk_report(
    platform: Any,
    profile: str,
    run_ids: Sequence[str],
    *,
    title: str = 'Model risk report',
    baseline: Optional[str] = None,
) -> Dict[str, Any]:
    """Assumptions, data, tests and limits of the given runs, as one JSON document.

    With two or more runs, the runs are also evaluated as entries named by run ID
    (``Platform.evaluation`` style figures and selection statistics are included when
    available). ``baseline`` names one of the runs to test the others against.
    """
    sections = [run_section(platform, profile, run_id) for run_id in run_ids]
    limits = list(GENERAL_LIMITS)
    if any(s.get('agent') for s in sections):
        limits.append(
            'Agent decisions depend on the model version served; replies are recorded, '
            'but a later call to the same model name may answer differently.'
        )
    if any(s['verification']['failed'] for s in sections):
        limits.insert(0, 'At least one run failed lineage verification; see its section.')
    report: Dict[str, Any] = dict(
        format=FORMAT,
        title=title,
        created_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        runs=sections,
        limits=limits,
    )
    if len(run_ids) >= 2:
        try:
            evaluation = platform.evaluation(
                profile, {run_id: [run_id] for run_id in run_ids}, baseline=baseline
            )
            report['evaluation'] = dict(
                metric=evaluation.get('metric'),
                entries=evaluation.get('entries'),
                overfitting=evaluation.get('overfitting'),
                warnings=evaluation.get('warnings'),
            )
        except Exception as exc:  # A report is still useful without the comparison.
            report['evaluation'] = dict(error=str(exc))
    return report


def _cell(value: Any) -> str:
    if value is None:
        return '-'
    if isinstance(value, float):
        return f'{value:.4g}'
    return str(value).replace('|', '\\|')


def render_markdown(report: Mapping[str, Any]) -> str:
    """A Markdown document of a model risk report, for review or archiving."""
    lines = [
        f'# {report["title"]}',
        '',
        f'Created {report["created_at"]} ({report["format"]}).',
        '',
    ]
    for section in report['runs']:
        lines += [f'## Run {section["run_id"]}', '']
        lines += [
            f'- Method: {section["method"]}; status: {section["status"]}',
            f'- Symbols: {", ".join(section.get("universe") or [])}',
            f'- Data dates: {" to ".join(section["dates"]) if section.get("dates") else "not dated"}',
            '',
            '### Assumptions',
            '',
        ]
        lines += [f'- {line}' for line in section['assumptions']] + ['']
        lines += ['### Data and code', '', '| Input | SHA-256 | Bytes |', '| --- | --- | --- |']
        for entry in section['data']:
            lines.append(
                f'| {_cell(entry.get("field"))} | {_cell(entry.get("sha256") or entry.get("error"))}'
                f' | {_cell(entry.get("bytes"))} |'
            )
        code = section.get('code') or {}
        lines += [
            '',
            f'Code: version {_cell(code.get("version"))}, commit {_cell(code.get("commit"))}'
            + (' (uncommitted changes)' if code.get('dirty') else '')
            + f', execution rules {_cell(code.get("rules_sha256"))}.',
            '',
        ]
        agent = section.get('agent')
        if agent:
            model = agent.get('model') or {}
            lines += [
                '### Agent and model',
                '',
                f'- Agent {_cell(agent.get("agent_id"))} revision {_cell(agent.get("revision"))}, '
                f'{agent.get("pacing")} pacing',
                f'- Model {_cell(model.get("provider"))} / {_cell(model.get("model"))}, '
                f'temperature {_cell(model.get("temperature"))}; served versions: '
                f'{", ".join(agent.get("served_models") or []) or "-"}',
                f'- Model calls {_cell(agent.get("model_calls"))} ({_cell(agent.get("failed_calls"))} '
                f'failed), tokens {_cell(agent.get("input_tokens"))} in / '
                f'{_cell(agent.get("output_tokens"))} out',
            ]
            if agent.get('budget_exceeded'):
                lines.append(f'- Ended by its model budget: {agent["budget_exceeded"]}')
            lines += [f'- Flag: {flag}' for flag in section.get('flags') or []] + ['']
        summary = section.get('summary') or {}
        verification = section['verification']
        lines += [
            '### Results and tests',
            '',
            '| Figure | Value |',
            '| --- | --- |',
        ]
        for key in sorted(summary):
            if isinstance(summary[key], (int, float, str)) or summary[key] is None:
                lines.append(f'| {key} | {_cell(summary[key])} |')
        lines += [
            '',
            '- Lineage verification: '
            + (
                'unchanged'
                if verification['unchanged']
                else 'CHANGED: ' + ', '.join(verification['failed'])
            )
            + ('' if verification['sealed'] else ' (records not sealed: run not finished)'),
            (
                '- Code now running differs from the run\'s code'
                if verification['code_changed']
                else '- Code now running matches the run\'s code'
            ),
            '',
        ]
    evaluation = report.get('evaluation')
    if evaluation:
        lines += ['## Comparison', '']
        if evaluation.get('error'):
            lines += [f'Not computed: {evaluation["error"]}', '']
        else:
            metric = evaluation.get('metric')
            lines += [
                f'| Entry | Mean {metric} | Probabilistic Sharpe | Deflated Sharpe | p (Holm) |',
                '| --- | --- | --- | --- | --- |',
            ]
            for entry in evaluation.get('entries') or []:
                selection = entry.get('selection') or {}
                mean = ((entry.get('statistics') or {}).get(metric) or {}).get('mean')
                p_value = (entry.get('comparison') or {}).get('p_value_holm')
                lines.append(
                    f'| {_cell(entry.get("name"))} | {_cell(mean)}'
                    f' | {_cell(selection.get("probabilistic_sharpe"))}'
                    f' | {_cell(selection.get("deflated_sharpe"))} | {_cell(p_value)} |'
                )
            overfitting = evaluation.get('overfitting') or {}
            if overfitting.get('probability') is not None:
                lines.append(
                    f'\nProbability of backtest overfitting: {overfitting["probability"]:.3f}'
                )
            lines += [f'- Warning: {w}' for w in evaluation.get('warnings') or []] + ['']
    lines += ['## Limits', ''] + [f'- {limit}' for limit in report['limits']] + ['']
    return '\n'.join(lines)
