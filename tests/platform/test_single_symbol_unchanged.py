"""Single-symbol results are exactly those of the code before several symbols existed.

The golden file was produced by running ``scenario_outputs`` on the commit before the
multi-symbol change (``python tests/platform/test_single_symbol_unchanged.py --write``
there). Generated identifiers are replaced by stable labels; everything else, every
fill, event, decision fingerprint, ledger total and Replay result, must match exactly.
"""

import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from sim_platform import Platform
from sim_platform.execution import ExecutionConfig

GOLDEN = ROOT / 'tests' / 'fixtures' / 'single_symbol_golden.json'
DAILY = """date,open,high,low,close,volume
2026-01-05,100.00,101.00,99.00,100.50,10000
2026-01-06,101.00,102.00,100.00,101.50,10000
2026-01-07,99.00,100.00,98.00,98.50,10000
"""
MODEL = '''
def decide(bars, account, context):
    return dict(target_position=10 if len(bars) % 2 else 0) if bars else None
'''
HEX = re.compile(r'[0-9a-f]{32}|[0-9a-f]{64}')


def stable(value, labels):
    """Replace generated identifiers by labels in order of first appearance."""
    if isinstance(value, dict):
        return {k: stable(v, labels) for k, v in value.items() if k not in ('mtime_ns',)}
    if isinstance(value, list):
        return [stable(v, labels) for v in value]
    if isinstance(value, str):
        return HEX.sub(lambda m: labels.setdefault(m.group(0), 'ID%d' % len(labels)), value)
    return value


def run_outputs(app, profile, account, run_id, root):
    labels = {}
    events = [
        dict(kind=e['kind'], time_ns=e['time_ns'], data=e['data'])
        for e in app.run_events(profile, run_id)
    ]
    snapshot = app.snapshot(profile, account)
    run = app.run(profile, run_id)
    records = run.get('records') or []
    replay = Platform.replay_run(root / 'app', run_id, root / ('replay-' + run_id))
    return stable(
        dict(
            events=events,
            cash=snapshot.cash,
            fees=snapshot.fees,
            realized_pnl=snapshot.realized_pnl,
            positions=[(p.symbol, p.quantity, p.cost) for p in snapshot.positions],
            orders=[
                (o.side, o.quantity, o.limit_price, o.filled, o.status) for o in snapshot.orders
            ],
            fingerprints=[r['fingerprint'] for r in records],
            results=[r.get('results') for r in records],
            replay=dict(
                verified=replay['verified'], fills=replay['fills'], decisions=replay['decisions']
            ),
        ),
        labels,
    )


def scenario_outputs(root):
    """Three single-symbol runs: ITCH rule, ITCH manual orders, daily CSV model."""
    from support.trading import fixture

    itch = fixture(root / 'feed.bin')
    csv = root / 'daily.csv'
    csv.write_text(DAILY)
    model = root / 'model.py'
    model.write_text(MODEL)
    app = Platform(root / 'app')
    profile = app.create_profile('Golden')
    outputs = {}
    plans = (
        ('itch_rule', dict(source=itch, method='rule')),
        ('itch_manual', dict(source=itch, method='human')),
        ('csv_model', dict(source=csv, method='model', settings=dict(model_file=str(model)))),
    )
    runs = []
    for name, plan in plans:
        account = app.create_account(profile, name, 100_000_000)
        experiment = app.open_experiment(
            profile,
            account,
            execution_mode='controlled',
            execution_config=ExecutionConfig(fee_per_share=100),
            **plan,
        )
        if name == 'itch_manual':
            experiment.step()
            experiment.order('buy-1', 'buy', 2, 1_000_000)
            experiment.order('buy-2', 'buy', 50, 990_000)
            experiment.step()
            experiment.cancel('buy-2')
            experiment.order('sell-1', 'sell', 1, 990_000)
        while experiment.runner.active:
            experiment.step()
        runs.append((name, account, experiment.run_id))
        experiment.close()
    for name, account, run_id in runs:
        outputs[name] = run_outputs(app, profile, account, run_id, root)
    app.close()
    return outputs


class SingleSymbolUnchangedTests(unittest.TestCase):
    def test_single_symbol_runs_match_the_results_before_several_symbols(self):
        with TemporaryDirectory(prefix='golden-') as folder:
            outputs = scenario_outputs(Path(folder))
        golden = json.loads(GOLDEN.read_text())
        for name in golden:
            with self.subTest(run=name):
                self.assertEqual(json.loads(json.dumps(outputs[name])), golden[name])


if __name__ == '__main__':
    if '--write' in sys.argv:
        with TemporaryDirectory(prefix='golden-') as folder:
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(json.dumps(scenario_outputs(Path(folder)), indent=1, sort_keys=True))
        print('wrote', GOLDEN)
    else:
        unittest.main()
