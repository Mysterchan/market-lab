"""Experiment lineage: recorded inputs, code and seals, and proof a run is unchanged."""

import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from sim_platform import Platform
from sim_platform.application.lineage import input_paths
from sim_platform.examples.configured_agent_demo import example_configuration
from sim_platform.examples.market_demo import write_fixture
from sim_platform.models.contracts import ModelProfile
from sim_platform.models.providers.rehearsal import RehearsalProvider
from sim_platform.persistence.lineage import LineageFiles

GRANTS = ['market.quote', 'control.finish', 'model.request', 'orders.submit_market']


class StoreTests(unittest.TestCase):
    def test_parts_are_written_once_and_folders_are_hashed(self):
        with TemporaryDirectory() as folder:
            files = LineageFiles(folder)
            self.assertTrue(files.write_once('run-1', 'inputs', dict(a=1)))
            self.assertFalse(files.write_once('run-1', 'inputs', dict(a=2)))
            self.assertEqual(files.read('run-1')['inputs'], dict(a=1))
            self.assertIsNone(files.read('run-1')['records'])
            data = Path(folder) / 'data'
            data.mkdir()
            (data / 'a.csv').write_text('x')
            first = files.digest(data)
            self.assertEqual(first['files'], 1)
            (data / 'b.csv').write_text('y')
            self.assertNotEqual(files.digest(data)['sha256'], first['sha256'])
            self.assertEqual(
                files.digest(data / 'a.csv')['sha256'], hashlib.sha256(b'x').hexdigest()
            )

    def test_input_paths_skip_replay_copies_and_caches(self):
        config = dict(
            research_source=dict(path='/a.bin', news_path='/n.json', prepared=dict(path='/x')),
            agent=dict(fundamentals_path='/f.csv', documents_path=None),
            replay=dict(research_source=dict(path='/a.bin')),
        )
        self.assertEqual(
            list(input_paths(config)),
            [
                ('agent.fundamentals_path', '/f.csv'),
                ('research_source.news_path', '/n.json'),
                ('research_source.path', '/a.bin'),
            ],
        )


class RunLineageTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source, _ = write_fixture(self.root)
        self.app = Platform(self.root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Research')

    def rule_run(self):
        account = self.app.create_account(self.profile, 'Cash', 10_000_000)
        experiment = self.app.open_experiment(
            self.profile,
            account,
            source=self.source,
            method='rule',
            execution_mode='controlled',
            settings=dict(quantity=1, hold_ns=10**9),
        )
        while experiment.runner.active:
            experiment.step()
        run_id = experiment.run_id
        experiment.close()
        return run_id

    def test_a_rule_run_records_inputs_code_and_a_seal(self):
        run_id = self.rule_run()
        lineage = self.app.run_lineage(self.profile, run_id)
        inputs = lineage['inputs']
        self.assertEqual(inputs['code']['version'], __import__('sim_platform').__version__)
        self.assertIn('rules_sha256', inputs['code'])
        recording = next(i for i in inputs['inputs'] if i['field'] == 'research_source.path')
        self.assertEqual(recording['sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(inputs['execution']['model'], 'displayed-limit-v1')
        self.assertEqual(lineage['records']['status'], 'completed')
        self.assertGreater(lineage['records']['events'], 0)
        proof = self.app.verify_run(self.profile, run_id)
        self.assertTrue(proof['unchanged'], proof['checks'])
        self.assertTrue(proof['sealed'])
        self.assertFalse(proof['code_changed'])

    def test_a_changed_recording_is_detected(self):
        run_id = self.rule_run()
        data = bytearray(self.source.read_bytes())
        data[-1] ^= 1
        self.source.write_bytes(bytes(data))
        proof = self.app.verify_run(self.profile, run_id)
        self.assertFalse(proof['unchanged'])
        failed = [c['item'] for c in proof['checks'] if not c['match']]
        self.assertEqual(failed, ['input research_source.path'])

    def test_a_changed_seal_file_cannot_be_replaced_through_the_store(self):
        run_id = self.rule_run()
        store = LineageFiles(self.root / 'app')
        self.assertFalse(store.write_once(run_id, 'records', dict(events=0)))
        # Editing the file by hand is visible: its seal no longer matches the events.
        path = store.root / run_id / 'records.json'
        os.chmod(path, 0o644)
        sealed = json.loads(path.read_text())
        path.write_text(json.dumps(dict(sealed, events_sha256='0' * 64)))
        proof = self.app.verify_run(self.profile, run_id)
        self.assertEqual([c['item'] for c in proof['checks'] if not c['match']], ['events'])

    def test_an_agent_run_records_its_model_and_log(self):
        definition, settings = example_configuration()
        definition['requires'] = settings['granted_tools'] = GRANTS
        self.app.create_agent(self.profile, definition)
        self.app.set_model_credential('key', 'unused')
        account = self.app.create_account(self.profile, 'Cash', 100_000_000)
        experiment = self.app.open_experiment(
            self.profile,
            account,
            source=self.source,
            method='agent',
            agent_id=definition['agent_id'],
            revision=1,
            settings=dict(settings, speed='turn'),
            execution_mode='live',
        )
        self.addCleanup(experiment.close)
        self.app.configure_experiment_model(
            experiment,
            ModelProfile('openai-responses', 'rehearsal', 'key', temperature=0),
            provider=RehearsalProvider('AAPL'),
        )
        experiment.play()
        started = time.monotonic()
        while experiment.runner.active or not experiment.runner.archive_ready:
            experiment.tick(render=False)
            time.sleep(0.01)
            self.assertLess(time.monotonic() - started, 60)
        run_id = experiment.run_id
        experiment.close()
        lineage = self.app.run_lineage(self.profile, run_id)
        agent = lineage['agent']
        self.assertEqual(agent['model']['model'], 'rehearsal')
        self.assertEqual(agent['model']['temperature'], 0)
        self.assertEqual(agent['served_models'], ['rehearsal'])
        self.assertEqual(len(agent['definition_sha256']), 64)
        proof = self.app.verify_run(self.profile, run_id)
        self.assertTrue(proof['unchanged'], [c for c in proof['checks'] if not c['match']])
        self.assertIn('agent log', [c['item'] for c in proof['checks']])


if __name__ == '__main__':
    unittest.main()
