"""Headless parity: Agents, experiments, evaluations and run reviews from the command line."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sim_platform.cli.main import main
from sim_platform.examples.configured_agent_demo import example_configuration
from sim_platform.examples.market_demo import write_fixture

GRANTS = [
    'market.quote',
    'control.finish',
    'model.request',
    'orders.submit_market',
    'journal.write',
]


class StudyCommandTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / 'data'
        self.source, _ = write_fixture(self.root)

    def invoke(self, *argv, expect=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(['--data-dir', str(self.data), *argv])
        self.assertEqual(status, expect, stderr.getvalue())
        return json.loads(stdout.getvalue()) if stdout.getvalue().strip() else None

    def config(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value))
        return str(path)

    def test_a_study_runs_end_to_end_from_the_command_line(self):
        rule = self.invoke(
            'experiment',
            '--config',
            self.config(
                'rule.json',
                dict(
                    profile_name='CLI',
                    cash_usd=1000,
                    source=str(self.source),
                    method='rule',
                    settings=dict(quantity=1, hold_ns=10**9),
                    execution=dict(fee_per_share=5),
                ),
            ),
        )
        self.assertEqual(rule['status'], 'completed')
        self.assertTrue(rule['verified']['unchanged'])
        profile = rule['profile']

        definition, settings = example_configuration()
        definition['requires'] = settings['granted_tools'] = GRANTS
        saved = self.invoke(
            'agent-import',
            '--profile',
            profile,
            '--definition',
            self.config('agent.json', definition),
        )
        self.assertEqual(saved['revision'], 1)
        listed = self.invoke('agents', '--profile', profile)
        self.assertEqual(len(listed), 1)
        agent = self.invoke(
            'experiment',
            '--config',
            self.config(
                'agent-run.json',
                dict(
                    profile=profile,
                    cash_usd=1000,
                    source=str(self.source),
                    method='agent',
                    agent_id=definition['agent_id'],
                    revision=1,
                    settings=dict(settings, speed='turn'),
                    model=dict(
                        provider='openai-compatible',
                        model='rehearsal',
                        base_url='http://127.0.0.1:9/v1',
                    ),
                    rehearsal='AAPL',
                    execution=dict(fee_per_share=5),
                ),
            ),
        )
        self.assertEqual(agent['status'], 'completed')
        run = agent['run_id']
        self.assertTrue(self.invoke('verify', '--profile', profile, '--run', run)['unchanged'])
        lineage = self.invoke('lineage', '--profile', profile, '--run', run)
        self.assertEqual(lineage['agent']['model']['provider'], 'openai-compatible')
        timeline = self.invoke('timeline', '--profile', profile, '--run', run)
        self.assertEqual(timeline['orders'][0]['tool'], 'orders.submit_market')
        diff = self.invoke('diff', '--profile', profile, rule['run_id'], run)
        self.assertTrue(diff['settings'])
        figures = self.invoke('attribution', '--profile', profile, '--run', rule['run_id'])
        self.assertIn('costs', figures)
        evaluation = self.invoke(
            'evaluate-runs',
            '--profile',
            profile,
            '--entry',
            f'rule={rule["run_id"]}',
            '--entry',
            f'agent={run}',
        )
        self.assertEqual(len(evaluation['entries']), 2)
        report = self.root / 'review.md'
        self.invoke(
            'risk-report',
            '--profile',
            profile,
            '--runs',
            rule['run_id'],
            run,
            '--output',
            str(report),
        )
        self.assertIn('## Limits', report.read_text())
        heat = self.root / 'heat.svg'
        result = self.invoke(
            'sweep',
            '--config',
            self.config(
                'sweep.json',
                dict(
                    recordings=[str(self.source)],
                    base=dict(name='rule', policy='rule'),
                    grid=dict(quantity=[1, 2], hold_ns=[10**9, 2 * 10**9]),
                    profile=profile,
                    cash=10_000_000,
                    resamples=200,
                ),
            ),
            '--heatmap',
            'quantity',
            'hold_ns',
            str(heat),
        )
        self.assertEqual(result['combinations'], 4)
        self.assertTrue(heat.read_text().startswith('<svg'))
        self.invoke('experiment', '--config', self.config('bad.json', dict(method='x')), expect=1)


if __name__ == '__main__':
    unittest.main()
