"""Experiment sweeps over parameter and prompt grids, with a table and a heat map."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sim_platform import ContractError, Platform
from sim_platform.examples.configured_agent_demo import example_configuration
from sim_platform.examples.market_demo import write_fixture
from sim_platform.models.providers.rehearsal import RehearsalProvider
from sim_platform.research.sweeps import expand_grid, heatmap, render_heatmap_svg

GRANTS = ['market.quote', 'control.finish', 'model.request', 'orders.submit_market']


def rehearsal():
    return RehearsalProvider('AAPL')


class GridTests(unittest.TestCase):
    def test_combinations_and_dotted_fields(self):
        combos = expand_grid(
            dict(name='x', policy='agent', settings=dict(speed='turn')),
            {'settings.budget.max_tokens': [10, 20], 'model.temperature': [0, 1]},
        )
        self.assertEqual(len(combos), 4)
        parameters, entry = combos[1]
        self.assertEqual(entry['settings'], dict(speed='turn', budget=dict(max_tokens=10)))
        self.assertEqual(entry['model'], dict(temperature=1))
        self.assertEqual(entry['name'], 'settings.budget.max_tokens=10, model.temperature=1')
        for bad in ({}, dict(name=[1]), dict(quantity=[])):
            with self.assertRaises(ContractError):
                expand_grid(dict(name='x'), bad)

    def test_heat_map_averages_other_fields(self):
        rows = [
            dict(parameters=dict(a=1, b='x', c=0), mean=1.0),
            dict(parameters=dict(a=1, b='x', c=1), mean=3.0),
            dict(parameters=dict(a=2, b='x', c=0), mean=-1.0),
            dict(parameters=dict(a=2, b='y', c=0), mean=None),
        ]
        data = heatmap(rows, 'a', 'b')
        self.assertEqual(data['x_values'], [1, 2])
        self.assertEqual(data['matrix'], [[2.0, -1.0], [None, None]])
        svg = render_heatmap_svg(data, title='Total return')
        self.assertTrue(svg.startswith('<svg'))
        self.assertIn('Total return', svg)


class SweepRunTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.source, _ = write_fixture(root)
        self.app = Platform(root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Research')

    def test_a_rule_grid_becomes_a_table(self):
        result = self.app.run_sweep(
            [self.source],
            dict(name='rule', policy='rule'),
            dict(quantity=[1, 2], hold_ns=[10**9, 2 * 10**9]),
            profile=self.profile,
            cash=10_000_000,
            resamples=200,
        )
        self.assertEqual(result['combinations'], 4)
        self.assertEqual(len(result['rows']), 4)
        row = result['rows'][0]
        self.assertEqual(row['parameters'], dict(quantity=1, hold_ns=10**9))
        recording = next(iter(row['recordings'].values()))
        self.assertEqual(recording['n'], 1)
        self.assertEqual(len(recording['run_ids']), 1)
        self.assertIsNotNone(result['best'])
        data = heatmap(result['rows'], 'quantity', 'hold_ns')
        self.assertEqual(len(data['matrix']), 2)

    def test_a_prompt_sweep_saves_one_revision_per_prompt(self):
        definition, settings = example_configuration()
        definition['requires'] = settings['granted_tools'] = GRANTS
        self.app.create_agent(self.profile, definition)
        self.app.set_model_credential('key', 'unused')
        base = dict(
            name='agent',
            policy='agent',
            agent_id=definition['agent_id'],
            revision=1,
            settings=dict(settings, speed='turn'),
            model=dict(provider='openai-responses', model='rehearsal', credential_ref='key'),
            repetitions=1,
        )
        result = self.app.run_sweep(
            [self.source],
            base,
            dict(prompt=['Buy one share.', 'Look, then finish.']),
            profile=self.profile,
            model_provider=rehearsal,
            resamples=200,
        )
        self.assertEqual(result['prompts'], {'2': 'Buy one share.', '3': 'Look, then finish.'})
        self.assertEqual(
            [row['parameters']['prompt'] for row in result['rows']],
            ['Buy one share.', 'Look, then finish.'],
        )
        saved = self.app.agent(self.profile, definition['agent_id'], 3)
        self.assertEqual(saved['definition']['initial_prompt'], 'Look, then finish.')
        with self.assertRaises(ContractError):
            self.app.run_sweep([self.source], dict(name='r'), dict(prompt=['x']))


if __name__ == '__main__':
    unittest.main()
