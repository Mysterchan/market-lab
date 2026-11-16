"""Model risk report and run diff of finished runs."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sim_platform import ContractError, Platform
from sim_platform.examples.market_demo import write_fixture
from sim_platform.execution import ExecutionConfig


class ModelRiskReportTests(unittest.TestCase):
    def test_two_rule_runs_make_one_report(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source, _ = write_fixture(root)
            with Platform(root / 'app') as app:
                profile = app.create_profile('Research')
                runs = []
                for quantity in (1, 2):
                    account = app.create_account(profile, 'Cash', 10_000_000)
                    experiment = app.open_experiment(
                        profile,
                        account,
                        source=source,
                        method='rule',
                        execution_mode='controlled',
                        settings=dict(quantity=quantity, hold_ns=10**9),
                        execution_config=ExecutionConfig(fee_per_share=5, impact_bps=3),
                    )
                    while experiment.runner.active:
                        experiment.step()
                    runs.append(experiment.run_id)
                    experiment.close()
                target = root / 'review.md'
                report = app.model_risk_report(profile, runs, path=target, baseline=runs[0])
                self.assertEqual(report['format'], 'market-lab-model-risk-report-v1')
                section = report['runs'][0]
                self.assertEqual(section['method'], 'rule')
                self.assertTrue(section['verification']['unchanged'])
                self.assertIn('Commission 0.0005 USD per share.', section['assumptions'])
                self.assertTrue(any('impact 3 bps' in line for line in section['assumptions']))
                self.assertEqual(len(section['data'][0]['sha256']), 64)
                self.assertEqual(len(report['evaluation']['entries']), 2)
                text = target.read_text()
                self.assertIn('# Model risk report', text)
                self.assertIn('## Limits', text)
                self.assertIn('Lineage verification: unchanged', text)
                self.assertIn('| Entry |', text)
                app.model_risk_report(profile, runs[:1], path=root / 'one.json')
                self.assertIn('"format"', (root / 'one.json').read_text())
                with self.assertRaises(ContractError):
                    app.model_risk_report(profile, [])
                diff = app.diff_runs(profile, runs[0], runs[1])
                self.assertEqual(diff['execution'], [])
                self.assertEqual(
                    [d['field'] for d in diff['settings']],
                    ['decision.policy.parameters.quantity'],
                )
                self.assertEqual(diff['data'], [])
                self.assertIsNone(diff['prompt'])
                fills = next(r for r in diff['results'] if r['figure'] == 'fill_count')
                self.assertEqual(fills['a'], fills['b'])


if __name__ == '__main__':
    unittest.main()
