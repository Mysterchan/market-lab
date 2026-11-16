"""Decision boundary ordering, limited observations, audit atomicity and adapters."""

from dataclasses import FrozenInstanceError
from pathlib import Path
import json
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim_platform import Platform, Intent, ContractError
from sim_platform.decisions import (
    BuyHoldSell,
    HumanPolicy,
    DecisionConfig,
    JSONProcessPolicy,
    RecordedPolicy,
    submit,
)
from sim_platform.examples.market_demo import write_fixture
from sim_platform.examples.decision_demo import run_decision_demo
from sim_platform.market import load_news
from sim_platform.persistence.sql.repositories import RunRepository

LIBRARY = None
if __name__ == '__main__' and len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
    LIBRARY = sys.argv.pop(1)


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='decision-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.app = Platform(self.path / 'accounts')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Owner')
        self.source, self.news = write_fixture(self.path / 'fixture')

    def scenario(self, policy=None, config=None, source=None):
        policy = policy or BuyHoldSell()
        account = self.app.create_account(self.profile, 'Account', 100_000_000)
        maker = self.app.create_maker(self.profile, policy.name, policy.kind, policy.version)
        run = self.app.create_decision_run(self.profile, account, maker, 'AAPL', policy, config)
        market = self.app.open_market('AAPL', library=LIBRARY)
        self.addCleanup(market.close)
        market.load(source or self.source, news=load_news(self.news))
        runner = self.app.decision_runner(self.profile, run, maker, market, policy)
        self.addCleanup(runner.close)
        return runner, run, account, maker

    def drain(self, runner):
        while runner.snapshot.status == 'running':
            runner.step()

    def test_demo_and_recorded_replay(self):
        result = run_decision_demo(self.path / 'demo', LIBRARY)
        self.assertTrue(result['recorded_replay_equal'])
        self.assertEqual(result['final']['cash'], 99_988_000)
        self.assertEqual(result['decision_count'], 8)

    def test_existing_orders_fill_before_observation_new_actions_wait(self):
        r, _, _, _ = self.scenario()
        for _ in range(3):
            r.step()
        self.assertEqual(r.snapshot.account.positions, ())
        self.assertEqual(r.snapshot.account.orders[0].status, 'accepted')
        r.step()  # news: no fill
        self.assertEqual(r.records[-1]['observation']['execution']['account']['positions'], [])
        r.step()  # E: fill before policy
        self.assertEqual(
            r.records[-1]['observation']['execution']['account']['positions'][0]['quantity'], 10
        )
        self.assertEqual(
            r.records[-1]['response']['state']['held_since'], r.market_snapshot.time_ns
        )

    def test_human_risk_and_cancel_share_protocol(self):
        r, run, account, _ = self.scenario(HumanPolicy(), DecisionConfig(mode='manual'))
        for _ in range(3):
            r.step()
        rejected = r.act([submit(Intent('large', 'AAPL', 'buy', 1000, 1_901_000))])
        self.assertEqual(rejected['results'][0]['status'], 'rejected')
        self.assertTrue(rejected['results'][0]['reason'])
        accepted = r.act([submit(Intent('small', 'AAPL', 'buy', 10, 1_901_000))])
        self.assertEqual(accepted['results'][0]['status'], 'accepted')
        cancelled = r.act([{'kind': 'cancel', 'client_id': 'small'}])
        self.assertEqual(cancelled['results'][0]['status'], 'cancelled')
        r.act([{'kind': 'cancel', 'client_id': 'unknown'}])
        self.assertEqual(r.records[-1]['results'][0]['status'], 'rejected')
        self.drain(r)
        self.assertEqual(r.snapshot.account.cash, 100_000_000)
        self.assertEqual(self.app.run(self.profile, run)['policy_state']['records'], r.records)
        self.assertEqual(
            self.app.replay_snapshot(self.profile, account, self.app.events(self.profile, account)),
            r.snapshot.account,
        )

    def test_pause_stops_time_and_decisions(self):
        r, _, _, _ = self.scenario()
        r.play()
        r.pump(2)
        r.pause()
        before = (r.snapshot, r.records)
        self.assertEqual(r.pump(20), ())
        self.assertEqual((r.snapshot, r.records), before)
        r.play()
        r.pump()
        self.assertEqual(len(r.records), 3)

    def test_manual_recording_replays_actions_and_cancel(self):
        config = DecisionConfig(mode='manual')
        r, _, _, _ = self.scenario(HumanPolicy(), config)
        for _ in range(3):
            r.step()
        r.act([submit(Intent('first', 'AAPL', 'buy', 10, 1_901_000))])
        r.act([{'kind': 'cancel', 'client_id': 'first'}])
        r.act([submit(Intent('second', 'AAPL', 'buy', 10, 1_901_000))])
        self.drain(r)
        policy = RecordedPolicy(r.records, kind='human')
        replay, _, _, _ = self.scenario(policy, config)
        for _ in range(3):
            replay.step()
        for _ in range(3):
            self.assertEqual(replay.decide()['status'], 'ok')
        self.drain(replay)
        policy.assert_consumed()
        self.assertEqual(replay.snapshot.account.cash, r.snapshot.account.cash)
        self.assertEqual(replay.snapshot.account.positions, r.snapshot.account.positions)

    def test_reopen_preserves_journal_without_resuming_market(self):
        r, run, account, _ = self.scenario()
        self.drain(r)
        with Platform(self.path / 'accounts') as reopened:
            self.assertEqual(reopened.run(self.profile, run)['policy_state']['records'], r.records)
            self.assertEqual(reopened.snapshot(self.profile, account), r.snapshot.account)

    def test_publication_visibility_and_immutable_observation(self):
        r, _, _, _ = self.scenario(config=DecisionConfig(history_limit=1))
        for _ in range(3):
            r.step()
        observation = r.observe()
        self.assertEqual(observation.news, ())
        with self.assertRaises(FrozenInstanceError):
            observation.boundary = 123
        r.step()
        self.assertEqual(len(r.observe().news), 1)
        self.drain(r)
        for record in r.records:
            obs = record['observation']
            self.assertTrue(
                all(n['available_time_ns'] <= obs['market']['time_ns'] for n in obs['news'])
            )
            self.assertTrue(
                all(t['timestamp_ns'] <= obs['market']['time_ns'] for t in obs['trades'])
            )
            self.assertLessEqual(len(obs['trades']), 1)

    def test_capability_rejected_before_run_creation(self):
        policy = BuyHoldSell()
        policy.requires = ('future_prices',)
        account = self.app.create_account(self.profile, 'Account', 10)
        maker = self.app.create_maker(self.profile, 'Rule', 'rule', '1')
        with self.assertRaisesRegex(ContractError, 'capability'):
            self.app.create_decision_run(self.profile, account, maker, 'AAPL', policy)
        self.assertEqual(self.app.runs(self.profile), [])

    def external(self, code):
        return JSONProcessPolicy([sys.executable, '-c', code])

    def test_external_json_valid_and_timeout(self):
        good = self.external(
            'import json,sys; r=json.load(sys.stdin); print(json.dumps({"actions":[],"state":{"time":r["observation"]["market"]["time_ns"]}}))'
        )
        r, _, _, _ = self.scenario(good)
        r.step()
        self.assertEqual(r.records[0]['status'], 'ok')
        self.assertEqual(r.records[0]['response']['state']['time'], r.snapshot.time_ns)
        r.close()
        slow = self.external('import time; time.sleep(10)')
        r, _, _, _ = self.scenario(slow, DecisionConfig(timeout_ms=50))
        r.step()
        self.assertEqual(r.records[0]['status'], 'timeout')
        self.assertEqual(r.snapshot.status, 'failed')

    def test_external_invalid_responses_apply_no_actions(self):
        for code in (
            'print("not json")',
            'raise RuntimeError("crash")',
            'print("x"*70000)',
            'print(\'{"actions": [], "state": {}, "future_time_ns": 999}\')',
        ):
            r, _, _, _ = self.scenario(self.external(code))
            r.step()
            self.assertEqual(r.records[0]['status'], 'error')
            self.assertEqual(r.snapshot.account.orders, ())
            self.assertEqual(r.snapshot.status, 'failed')

    def test_whole_response_validation_and_action_budget(self):
        for actions in (
            [submit(Intent('valid', 'AAPL', 'buy', 10, 1_901_000)), {'kind': 'fill'}],
            [submit(Intent('foreign', 'MSFT', 'buy', 10, 1))],
            [submit(Intent(str(i), 'AAPL', 'buy', 1, 1)) for i in range(9)],
        ):
            r, _, _, _ = self.scenario(HumanPolicy(), DecisionConfig(mode='manual'))
            for _ in range(3):
                r.step()
            result = r.act(actions)
            self.assertEqual(result['status'], 'error')
            self.assertEqual(r.snapshot.account.orders, ())

    def test_no_public_state_or_execution_bypass(self):
        r, run, _, maker = self.scenario(HumanPolicy(), DecisionConfig(mode='manual'))
        with self.assertRaisesRegex(ContractError, 'DecisionRunner'):
            self.app.execution_session(self.profile, run, maker, r._market)
        with self.assertRaisesRegex(ContractError, 'owns'):
            self.app.decision_session(self.profile, run, maker).save_state({})
        with self.assertRaisesRegex(ContractError, 'DecisionRunner'):
            r._execution.submit(Intent('bypass', 'AAPL', 'buy', 1, 1))

    def test_atomic_audit_and_actions_on_storage_failure(self):
        r, run, account, _ = self.scenario(HumanPolicy(), DecisionConfig(mode='manual'))
        for _ in range(3):
            r.step()
        with patch.object(RunRepository, 'save_policy_state', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                r.act([submit(Intent('buy', 'AAPL', 'buy', 10, 1_901_000))])
        self.assertEqual(r.snapshot.status, 'failed')
        self.assertEqual(r.snapshot.account.orders, ())
        self.assertEqual(r.records, [])
        self.assertFalse(any(e['kind'] == 'intent' for e in self.app.events(self.profile, account)))

    def test_replay_detects_changed_observation(self):
        r, _, _, _ = self.scenario()
        self.drain(r)
        records = r.records
        records[0]['fingerprint'] = 'changed'
        replay = RecordedPolicy(records)
        other, _, _, _ = self.scenario(replay)
        other.step()
        self.assertEqual(other.snapshot.status, 'failed')
        self.assertIn('mismatch', other.records[0]['error'])

    def test_trigger_interval_and_decision_budget(self):
        r, _, _, _ = self.scenario(
            config=DecisionConfig(triggers=('market',), interval_ns=3_000_000_000)
        )
        self.drain(r)
        times = [rec['observation']['market']['time_ns'] for rec in r.records]
        self.assertTrue(all(b - a >= 3_000_000_000 for a, b in zip(times, times[1:])))
        self.assertTrue(all(rec['observation']['trigger'] == 'market' for rec in r.records))
        r, _, _, _ = self.scenario(config=DecisionConfig(max_decisions=1))
        r.step()
        with self.assertRaisesRegex(ContractError, 'budget'):
            r.step()
        self.assertEqual(r.snapshot.status, 'failed')

    def test_eof_cancels_without_extra_policy_decision(self):
        r, _, _, _ = self.scenario(HumanPolicy(), DecisionConfig(mode='manual'))
        for _ in range(3):
            r.step()
        r.act([submit(Intent('passive', 'AAPL', 'buy', 10, 1))])
        self.drain(r)
        self.assertEqual(r.snapshot.status, 'completed')
        self.assertEqual(r.snapshot.account.orders[0].status, 'cancelled')
        self.assertEqual(len(r.records), 1)

    def test_feed_failure_stops_before_policy_and_cancels(self):
        broken = self.path / 'truncated.bin'
        broken.write_bytes(self.source.read_bytes()[:-2] + b'\0')
        r, _, _, _ = self.scenario(HumanPolicy(), DecisionConfig(mode='manual'), broken)
        for _ in range(3):
            r.step()
        r.act([submit(Intent('passive', 'AAPL', 'buy', 10, 1))])
        self.drain(r)
        self.assertEqual(r.snapshot.status, 'failed')
        self.assertEqual(r.snapshot.account.orders[0].status, 'cancelled')
        self.assertEqual(len(r.records), 1)

    def test_timeout_cancels_existing_order_and_rejects_late_actions(self):
        policy = self.external('import time; time.sleep(10)')
        r, _, _, _ = self.scenario(policy, DecisionConfig(mode='manual', timeout_ms=50))
        for _ in range(3):
            r.step()
        r.act([submit(Intent('passive', 'AAPL', 'buy', 10, 1))])
        self.assertEqual(r.decide()['status'], 'timeout')
        self.assertEqual(r.snapshot.account.orders[0].status, 'cancelled')
        with self.assertRaisesRegex(ContractError, 'closed'):
            r.act([submit(Intent('late', 'AAPL', 'buy', 10, 1_901_000))])

    def test_rerunning_rule_produces_same_observations_and_actions(self):
        first, _, _, _ = self.scenario()
        self.drain(first)
        second, _, _, _ = self.scenario()
        self.drain(second)
        self.assertEqual(
            [r['fingerprint'] for r in first.records], [r['fingerprint'] for r in second.records]
        )
        self.assertEqual(
            [r['response'] for r in first.records], [r['response'] for r in second.records]
        )

    def test_cli_demo(self):
        result = subprocess.run(
            [sys.executable, '-m', 'sim_platform', 'decision-demo']
            + (['--library', LIBRARY] if LIBRARY else []),
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertTrue(json.loads(result.stdout)['recorded_replay_equal'])

    def test_cli_human_and_rule_use_existing_account(self):
        for kind, commands in (
            ('human', 'step 3;buy cli-buy 10 190.10;run;decisions'),
            ('rule', 'run;decisions'),
        ):
            account = self.app.create_account(self.profile, kind, 100_000_000)
            maker = self.app.create_maker(self.profile, kind, kind, '1')
            result = subprocess.run(
                [
                    sys.executable,
                    '-m',
                    'sim_platform',
                    '--data-dir',
                    str(self.path / 'accounts'),
                    'decision',
                    '--profile',
                    self.profile,
                    '--account',
                    account,
                    '--maker',
                    maker,
                    '--input',
                    str(self.source),
                    '--policy',
                    kind,
                    '--commands',
                    commands,
                ]
                + (['--library', LIBRARY] if LIBRARY else []),
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(output[-1]['execution']['status'], 'completed')
            self.assertTrue(self.app.runs(self.profile)[-1]['policy_state']['records'])


if __name__ == '__main__':
    unittest.main()
