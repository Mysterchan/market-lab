"""Consistent historical run reports and atomic JSON/CSV exports, without Qt."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Callable, Dict, Iterable, Optional

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError, MONEY_SCALE

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.market.native import PathInput

# Report data read from the database: 'run', 'run_events', 'account_ledger', 'final'
# and optionally 'last_market', 'decision_tail', 'fill_count', 'decision_count'.
ReportData = Dict[str, Any]
# A 'trading-research-v1' report (see format_report) or a 'trading-comparison-v1'
# comparison with 'results'; values may be StreamArray while exporting.
Report = Dict[str, Any]
# Writes one file atomically: writer(path, serialize) calls serialize(stream) and
# returns the written path as text.
ExportWriter = Callable[[Path, Callable[[IO[str]], None]], str]


def run_report(app: Any, profile: str, run_id: str) -> Report:
    """Build the full report for a run; ``app`` is application's DatabaseOperations."""
    data = app.report_data(profile, run_id)
    return format_report(data)


def report_view(app: Any, profile: str, run_id: str) -> Report:
    """Build the bounded report used for display; ``app`` is DatabaseOperations."""
    return format_report(app.report_view_data(profile, run_id))


def format_report(data: ReportData) -> Report:
    """Turn stored run data into a detached report with a summary and assumptions."""
    run, own, prefix, final = (data[k] for k in ('run', 'run_events', 'account_ledger', 'final'))
    ticks = [e for e in own if e['kind'] == 'execution_tick']
    market = data.get('last_market', ticks[-1]['data']['market'] if ticks else None)
    journal = run['policy_state']
    fills = [e for e in own if e['kind'] == 'fill']
    mark = market['bids'][0][0] if market and market['bids'] else None
    equity = (
        final['cash'] + sum(p['quantity'] * mark for p in final['positions'])
        if (mark is not None and all(p['symbol'] == market['symbol'] for p in final['positions']))
        else (final['cash'] if not final['positions'] else None)
    )
    return copy_json(
        dict(
            format='trading-research-v1',
            money_scale=MONEY_SCALE,
            currency='USD',
            run=run,
            initial=run['initial_snapshot'],
            final=final,
            market=market,
            source=run['config'].get('research_source'),
            account_ledger=prefix,
            run_events=own,
            decisions=data.get('decision_tail', journal.get('records', [])),
            handoffs=journal.get('handoffs', []),
            authority_rejections=journal.get('authority_rejections', []),
            market_data=journal.get('research_archive', {}),
            summary=dict(
                status=run['status'],
                cash=final['cash'],
                realized_pnl=final['realized_pnl'],
                fees=final['fees'],
                run_realized_pnl=final['realized_pnl'] - run['initial_snapshot']['realized_pnl'],
                run_fees=final['fees'] - run['initial_snapshot']['fees'],
                position_quantity=sum(p['quantity'] for p in final['positions']),
                fill_count=data.get('fill_count', len(fills)),
                decision_count=data.get('decision_count', len(journal.get('records', []))),
                handoff_count=len(journal.get('handoffs', [])),
                estimated_equity_at_bid=equity,
                valuation='last observed best bid; not guaranteed liquidation',
            ),
            assumptions=[
                'displayed-limit-v1 simulated execution',
                'no passive queue or broker',
                'historical account ledger prefix',
                'equity is an estimate; report is not a performance guarantee',
            ],
        )
    )


class StreamArray:
    """Marks a large list that write_json streams item by item instead of copying.

    Created by export_run for one export; it only wraps the iterable it was given.
    """

    def __init__(self, values: Iterable[Any]) -> None:
        self.values = values


def write_json(stream: IO[str], value: Any) -> None:
    """Write a JSON value to ``stream``, streaming any StreamArray contents."""
    if isinstance(value, dict):
        stream.write('{')
        for index, (key, item) in enumerate(value.items()):
            if index:
                stream.write(',')
            stream.write(json.dumps(key, ensure_ascii=False) + ':')
            write_json(stream, item)
        stream.write('}')
    elif isinstance(value, (list, tuple, StreamArray)):
        stream.write('[')
        values = value.values if isinstance(value, StreamArray) else value
        for index, item in enumerate(values):
            if index:
                stream.write(',')
            write_json(stream, item)
        stream.write(']')
    else:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False))


def export_run(
    app: Any,
    profile: str,
    run_id: str,
    path: PathInput,
    *,
    writer: Optional[ExportWriter] = None,
) -> str:
    """Export a run's report while streaming its large record lists from the database."""
    with app.report_stream(profile, run_id) as (data, arrays):
        report = format_report(data)
        journal = report['run']['policy_state']
        if '_record_count' in journal:
            journal.pop('_record_count')
            journal['records'] = StreamArray(arrays.pop('nested_decisions'))
        for key, values in arrays.items():
            if key != 'nested_decisions':
                report[key] = StreamArray(values)
        return export_report(report, path, writer=writer)


def export_report(report: Report, path: PathInput, *, writer: Optional[ExportWriter] = None) -> str:
    """Write a report as JSON (or its run events as CSV) without overwriting an input file."""
    path = Path(path)
    if not path.is_absolute():
        raise ContractError('report destination must be absolute')
    sources = [report.get('source')] + [
        r.get('report', {}).get('source') for r in report.get('results', [])
    ]
    for source in sources:
        if source and any(
            Path(source[key]).resolve() == path.resolve()
            for key in ('path', 'news_path')
            if source.get(key)
        ):
            raise ContractError('report destination cannot overwrite an input')
    if path.suffix.lower() not in ('.json', '.csv'):
        raise ContractError('report extension must be .json or .csv')
    if path.suffix.lower() == '.csv' and 'run_events' not in report:
        raise ContractError('comparison export requires .json')

    def serialize(stream: IO[str]) -> None:
        if path.suffix.lower() == '.json':
            write_json(stream, report)
            stream.write('\n')
        else:
            output = csv.writer(stream)
            output.writerow(['sequence', 'simulation_time_ns', 'kind', 'data_json', 'money_scale'])
            events = report['run_events']
            for event in events.values if isinstance(events, StreamArray) else events:
                output.writerow(
                    [
                        event['sequence'],
                        event['time_ns'],
                        event['kind'],
                        json.dumps(
                            event['data'], ensure_ascii=False, sort_keys=True, allow_nan=False
                        ),
                        MONEY_SCALE,
                    ]
                )

    if writer is None:
        # Legacy standalone export API uses the same persistence adapter.
        from sim_platform.persistence.export_files import atomic_write

        writer = atomic_write
    return writer(path, serialize)
