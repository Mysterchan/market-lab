"""Authority epochs, retained orders, atomic state and scheduled boundaries."""

from dataclasses import asdict
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
    HumanPolicy,
    HoldThenSell,
    DecisionConfig,
    RecordedPolicy,
    StaleAuthority,
    submit,
)
from sim_platform.examples.market_demo import write_fixture
from sim_platform.examples.handoff_demo import run_handoff_demo
from sim_platform.market import load_news
from sim_platform.persistence.sql.repositories import RunRepository

LIBRARY = None
if __name__ == '__main__' and len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
    LIBRARY = sys.argv.pop(1)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='handoff-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.app = Platform(self.path / 'accounts')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Owner')
        self.account = self.app.create_account(self.profile, 'Account', 100_000_000)
        self.human = HumanPolicy()
        self.first = self.maker(self.human)
        self.rule = HoldThenSell()
        self.second = self.maker(self.rule)
        source, news = write_fixture(self.path / 'fixture')
        self.market = self.app.open_market('AAPL', library=LIBRARY)
        self.addCleanup(self.market.close)
        self.market.load(source, news=load_news(news))
        self.run = self.app.create_decision_run(
            self.profile,
            self.account,
            self.first,
            'AAPL',
            self.human,
            DecisionConfig(mode='manual'),
        )
        self.r = self.app.decision_runner(
            self.profile, self.run, self.first, self.market, self.human
        )
        self.addCleanup(self.r.close)
        for _ in range(3):
            self.r.step()
        self.start = self.market.time_ns - 2_000_000_000

    def maker(self, policy):
        return self.app.create_maker(self.profile, policy.name, policy.kind, policy.version)

    def buy(self, quantity=10, client='buy'):
        return self.r.act([submit(Intent(client, 'AAPL', 'buy', quantity, 1_901_000))])

    def fill(self):
        self.r.step()  # news
        self.r.step()  # E

    def drain(self):
        while self.r.snapshot.status == 'running':
            self.r.step()

    def test_manual_handoff_preserves_positions_open_orders_and_cash(self):
        self.buy()
        self.fill()
        self.r.act([submit(Intent('passive', 'AAPL', 'buy', 1, 1))])
        before = self.r.snapshot
        record = self.r.handoff(self.second, self.rule)
        self.assertEqual(self.r.snapshot, before)
        self.assertEqual(record['account'], json.loads(json.dumps(asdict(before.account))))
        self.assertEqual(self.app.run(self.profile, self.run)['maker_id'], self.second)
        self.assertEqual(record['generation'], 1)
        self.drain()
        self.assertEqual(self.r.snapshot.account.positions, ())
        passive = next(
            o for o in self.r.snapshot.account.orders if o.intent_id.endswith(':intent:passive')
        )
        self.assertEqual(passive.status, 'cancelled')

    def test_new_owner_can_cancel_inherited_order(self):
        self.buy()
        other = HumanPolicy()
        target = self.maker(other)
        self.r.handoff(target, other)
        result = self.r.controller().act([{'kind': 'cancel', 'client_id': 'buy'}])
        self.assertEqual(result['results'][0]['status'], 'cancelled')
        self.assertEqual(self.r.snapshot.reserved_cash, 0)

    def test_partial_order_and_remaining_reservations_survive_handoff(self):
        account = self.app.create_account(self.profile, 'Larger account', 200_000_000)
        run = self.app.create_decision_run(
            self.profile, account, self.first, 'AAPL', self.human, DecisionConfig(mode='manual')
        )
        with self.app.open_market('AAPL', library=LIBRARY) as market:
            market.load(self.path / 'fixture/aapl.bin')
            with self.app.decision_runner(
                self.profile, run, self.first, market, self.human
            ) as runner:
                for _ in range(3):
                    runner.step()
                runner.act([submit(Intent('large', 'AAPL', 'buy', 90, 1_901_000))])
                runner.step()  # E: visible ask only supplies 80
                before = runner.snapshot
                self.assertEqual(before.account.orders[0].status, 'partial')
                self.assertEqual(before.account.orders[0].filled, 80)
                self.assertEqual(before.reserved_cash, 19_010_000)
                runner.handoff(self.second, self.rule)
                self.assertEqual(runner.snapshot, before)

    def test_automatic_handoff_storage_failure_stops_with_old_owner(self):
        self.buy()
        self.r.schedule_handoff(
            self.second, self.rule, {'kind': 'position_at_least', 'symbol': 'AAPL', 'quantity': 10}
        )
        self.r.step()  # news
        with patch.object(RunRepository, 'set_decision_owner', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                self.r.step()  # old order fill commits; handoff rolls back and stops
        self.assertEqual(self.r.snapshot.status, 'failed')
        self.assertEqual(self.app.run(self.profile, self.run)['maker_id'], self.first)
        self.assertEqual(self.r.handoffs, [])
        self.assertEqual(self.r.snapshot.account.positions[0].quantity, 10)

    def test_stale_handle_and_response_do_not_change_account_or_stop_run(self):
        old = self.r.controller()
        request = old.prepare()
        target = self.maker(HumanPolicy())
        self.r.handoff(target, HumanPolicy())
        current = self.r.prepare()
        before = self.r.snapshot
        with self.assertRaises(StaleAuthority):
            old.act([submit(Intent('late', 'AAPL', 'buy', 1, 1_901_000))])
        with self.assertRaises(StaleAuthority):
            self.r.respond(request, {'actions': [], 'state': {}})
        self.assertEqual(self.r.snapshot, before)
        self.assertEqual(
            self.r.respond(current, {'actions': [], 'state': {'active': True}})['status'], 'ok'
        )
        journal = self.app.run(self.profile, self.run)['policy_state']
        self.assertEqual(len(journal['authority_rejections']), 2)

    def test_return_to_original_maker_still_rejects_old_generation(self):
        old = self.r.controller()
        request = old.prepare()
        self.r.handoff(self.second, self.rule)
        self.r.handoff(self.first, self.human)
        with self.assertRaises(StaleAuthority):
            old.act([])
        with self.assertRaises(StaleAuthority):
            self.r.respond(request, {'actions': [], 'state': {}})
        self.assertEqual(self.r.controller().act([])['generation'], 2)

    def test_reset_and_restore_saved_state(self):
        self.r.act([], state={'memory': 42})
        self.r.handoff(self.second, self.rule)
        self.assertEqual(self.app.run(self.profile, self.run)['policy_state']['state'], {})
        self.r.handoff(self.first, self.human, state_mode='restore')
        self.assertEqual(
            self.app.run(self.profile, self.run)['policy_state']['state'], {'memory': 42}
        )
        self.assertEqual(self.r.handoffs[-1]['incoming_state'], {'memory': 42})
        self.r.handoff(self.second, self.rule)
        self.r.handoff(self.first, self.human, state_mode='reset')
        self.assertEqual(self.app.run(self.profile, self.run)['policy_state']['state'], {})

    def test_incompatible_or_missing_restore_rejected_without_handoff(self):
        with self.assertRaisesRegex(ContractError, 'compatible'):
            self.r.handoff(self.second, self.rule, state_mode='restore')
        self.r.act([], state={'memory': 42})
        self.r.handoff(self.second, self.rule)
        changed = HumanPolicy()
        changed.version = '2'
        before = self.app.run(self.profile, self.run)
        with self.assertRaisesRegex(ContractError, 'compatible'):
            self.r.handoff(self.first, changed, state_mode='restore')
        self.assertEqual(self.app.run(self.profile, self.run), before)

    def test_wrong_owner_kind_capability_and_same_maker_rejected(self):
        outsider = self.app.create_profile('Other')
        foreign = self.app.create_maker(outsider, 'Rule', 'rule', '1')
        before = self.app.run(self.profile, self.run)
        for maker, policy in (
            (foreign, self.rule),
            (self.first, self.rule),
            (self.first, self.human),
        ):
            with self.assertRaises(ContractError):
                self.r.handoff(maker, policy)
        unsupported = HoldThenSell()
        unsupported.requires = ('future',)
        with self.assertRaisesRegex(ContractError, 'capability'):
            self.r.handoff(self.second, unsupported)
        self.assertEqual(self.app.run(self.profile, self.run), before)

    def test_transaction_failure_keeps_old_authority_state_and_orders(self):
        self.buy()
        before = self.app.run(self.profile, self.run)
        snapshot = self.r.snapshot
        old = self.r.controller()
        original = RunRepository.set_decision_owner

        def fail_after_write(repo, *args):
            original(repo, *args)
            raise OSError('storage failed after flush')

        with patch.object(RunRepository, 'set_decision_owner', fail_after_write):
            with self.assertRaises(OSError):
                self.r.handoff(self.second, self.rule)
        self.assertEqual(self.app.run(self.profile, self.run), before)
        self.assertEqual(self.r.snapshot, snapshot)
        self.assertEqual(old.act([])['status'], 'ok')
        self.assertEqual(self.r.handoff(self.second, self.rule)['generation'], 1)

    def test_time_switch_uses_first_event_at_or_after_time_and_observes_fill(self):
        self.buy()
        self.r.schedule_handoff(
            self.second, self.rule, {'kind': 'time', 'time_ns': self.start + 3_500_000_000}
        )
        self.r.step()  # news at +3: not yet
        self.assertEqual(self.r.handoffs, [])
        self.r.step()  # +4 fill, then switch, then new decision
        record = self.r.handoffs[0]
        self.assertEqual(record['time_ns'], self.start + 4_000_000_000)
        self.assertEqual(record['account']['positions'][0]['quantity'], 10)
        self.assertEqual(self.r.records[-1]['generation'], 1)
        self.assertEqual(
            self.r.records[-1]['observation']['execution']['account']['positions'][0]['quantity'],
            10,
        )
        self.assertEqual(self.r.records[-1]['response']['state']['held_since'], record['time_ns'])

    def test_position_switch_checks_account_after_fill(self):
        self.buy()
        self.r.schedule_handoff(
            self.second, self.rule, {'kind': 'position_at_least', 'symbol': 'AAPL', 'quantity': 10}
        )
        self.r.step()
        self.assertEqual(self.r.handoffs, [])
        self.r.step()
        self.assertEqual(self.r.handoffs[0]['reason'], 'position_at_least')
        self.assertEqual(self.r.snapshot.account.positions[0].quantity, 10)

    def test_automatic_handoff_and_recorded_actions_replay_same_boundary(self):
        self.buy()
        condition = {'kind': 'position_at_least', 'symbol': 'AAPL', 'quantity': 10}
        self.r.schedule_handoff(self.second, self.rule, condition)
        self.drain()
        records = self.r.records
        first = RecordedPolicy([r for r in records if r['generation'] == 0], kind='human')
        second = RecordedPolicy([r for r in records if r['generation'] == 1])
        account = self.app.create_account(self.profile, 'Replay', 100_000_000)
        maker, target = self.maker(first), self.maker(second)
        run = self.app.create_decision_run(
            self.profile, account, maker, 'AAPL', first, DecisionConfig(mode='manual')
        )
        with self.app.open_market('AAPL', library=LIBRARY) as market:
            market.load(
                self.path / 'fixture/aapl.bin', news=load_news(self.path / 'fixture/news.json')
            )
            with self.app.decision_runner(self.profile, run, maker, market, first) as replay:
                for _ in range(3):
                    replay.step()
                replay.decide()
                replay.schedule_handoff(target, second, condition)
                while replay.snapshot.status == 'running':
                    replay.step()
                self.assertEqual(
                    [r['fingerprint'] for r in replay.records], [r['fingerprint'] for r in records]
                )
                self.assertEqual(replay.handoffs[0]['boundary'], self.r.handoffs[0]['boundary'])
                self.assertEqual(replay.handoffs[0]['reason'], self.r.handoffs[0]['reason'])
                self.assertEqual(replay.snapshot.account.cash, self.r.snapshot.account.cash)
                self.assertEqual(
                    replay.snapshot.account.positions, self.r.snapshot.account.positions
                )
        first.assert_consumed()
        second.assert_consumed()

    def test_pause_does_not_fire_scheduled_switch(self):
        self.r.schedule_handoff(
            self.second, self.rule, {'kind': 'time', 'time_ns': self.market.time_ns}
        )
        before = self.r.snapshot
        self.assertEqual(self.r.pump(10), ())
        self.assertEqual(self.r.handoffs, [])
        self.assertEqual(self.r.snapshot, before)
        self.r.play()
        self.r.pump()
        self.assertEqual(len(self.r.handoffs), 1)

    def test_first_matching_switch_wins_and_other_schedules_invalidated(self):
        condition = {'kind': 'time', 'time_ns': self.market.time_ns}
        self.r.schedule_handoff(self.second, self.rule, condition)
        third = self.maker(HumanPolicy())
        self.r.schedule_handoff(third, HumanPolicy(), condition)
        self.r.step()
        switches = self.app.run(self.profile, self.run)['policy_state']['switches']
        self.assertEqual([s['status'] for s in switches], ['fired', 'invalidated'])
        self.assertEqual(self.app.run(self.profile, self.run)['maker_id'], self.second)

    def test_manual_handoff_invalidates_old_schedules(self):
        self.r.schedule_handoff(
            self.second, self.rule, {'kind': 'time', 'time_ns': self.start + 6_000_000_000}
        )
        target = self.maker(HumanPolicy())
        self.r.handoff(target, HumanPolicy())
        self.drain()
        self.assertEqual(len(self.r.handoffs), 1)
        self.assertEqual(
            self.app.run(self.profile, self.run)['policy_state']['switches'][0]['status'],
            'invalidated',
        )

    def test_bad_conditions_and_stale_observation_rejected(self):
        for condition in (
            {'kind': 'price'},
            {'kind': 'time', 'time_ns': -1},
            # Market time may span many days; only a time beyond the 100-year bound is invalid.
            {'kind': 'time', 'time_ns': 36_525 * 86_400_000_000_000},
            {'kind': 'position_at_least', 'symbol': 'MSFT', 'quantity': 1},
        ):
            with self.assertRaises(ContractError):
                self.r.schedule_handoff(self.second, self.rule, condition)
        request = self.r.prepare()
        self.r.step()
        with self.assertRaisesRegex(StaleAuthority, 'observation'):
            self.r.respond(request, {'actions': [], 'state': {}})
        self.assertEqual(self.r.snapshot.status, 'running')
        rejections = self.app.run(self.profile, self.run)['policy_state']['authority_rejections']
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0]['reason'], 'stale decision observation')

    def test_null_delayed_response_is_invalid_not_an_adapter_invocation(self):
        self.assertEqual(self.r.respond(self.r.prepare(), None)['status'], 'error')
        self.assertEqual(self.r.snapshot.status, 'failed')

    def test_inherited_sell_reservations_prevent_duplicate_sales(self):
        self.buy()
        self.fill()
        self.r.act([submit(Intent('old-sell', 'AAPL', 'sell', 4, 9_999_999))])
        self.r.handoff(self.second, HoldThenSell(hold_ns=0))
        self.r.step()
        action = self.r.records[-1]['response']['actions'][0]
        self.assertEqual(action['intent']['quantity'], 6)
        self.drain()
        self.assertEqual(self.r.snapshot.account.positions[0].quantity, 4)

    def test_reopen_preserves_owner_epoch_handoffs_and_state(self):
        self.r.act([], state={'memory': 42})
        self.r.handoff(self.second, self.rule)
        expected = self.app.run(self.profile, self.run)
        with Platform(self.path / 'accounts') as reopened:
            self.assertEqual(reopened.run(self.profile, self.run), expected)
            self.assertEqual(reopened.snapshot(self.profile, self.account), self.r.snapshot.account)

    def test_handoff_inside_policy_rejected(self):
        class BusyPolicy(HoldThenSell):
            def decide(policy, observation, state, timeout_ms):
                self.r.handoff(self.first, self.human)

        policy = BusyPolicy()
        self.r.handoff(self.second, policy)
        self.r.step()
        self.assertEqual(self.r.snapshot.status, 'failed')
        self.assertIn('completed decision boundary', self.r.records[-1]['error'])
        self.assertEqual(len(self.r.handoffs), 1)

    def test_demo_replay_and_cli(self):
        result = run_handoff_demo(self.path / 'demo', LIBRARY)
        self.assertTrue(result['recorded_replay_equal'])
        self.assertTrue(result['existing_orders_preserved'])
        self.assertEqual(result['final']['cash'], 99_988_000)
        process = subprocess.run(
            [sys.executable, '-m', 'sim_platform', 'handoff-demo']
            + (['--library', LIBRARY] if LIBRARY else []),
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertTrue(json.loads(process.stdout)['recorded_replay_equal'])

    def test_cli_manual_and_scheduled_handoff(self):
        self.r.close()
        for command in (
            f'handoff {self.second} hold',
            f'switch-position 10 {self.second} hold',
            f'switch-at {self.start + 4_000_000_000} {self.second} hold',
        ):
            account = self.app.create_account(self.profile, 'CLI', 100_000_000)
            process = subprocess.run(
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
                    self.first,
                    '--input',
                    str(self.path / 'fixture/aapl.bin'),
                    '--policy',
                    'human',
                    '--hold-ns',
                    '1000000000',
                    '--commands',
                    f'step 3;buy cli-buy 10 190.10;{command};run;handoffs',
                ]
                + (['--library', LIBRARY] if LIBRARY else []),
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            output = [json.loads(line) for line in process.stdout.splitlines()]
            self.assertEqual(output[-1]['maker_id'], self.second)
            self.assertEqual(output[-1]['execution']['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
