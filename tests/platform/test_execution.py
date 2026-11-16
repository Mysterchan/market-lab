"""Execution verification against independently calculated amounts and persistent ledger replay."""

from pathlib import Path
import json
import subprocess
import sys
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from itch_fixtures import add, reduce, frame, common
from support.execution import ExecutionFixture
from sim_platform import Platform, Intent, ContractError
from sim_platform.application.use_cases.runs import RunService
from sim_platform.domain.ledger import replay, apply_event
from sim_platform.domain.models import LedgerEvent
from sim_platform.examples.fixture_executor import FixtureExecutor
from sim_platform.examples.execution_demo import run_execution_demo
from sim_platform.execution import ExecutionConfig
from sim_platform.market import SequencedFeed, FeedRecord, NewsRecord

LIBRARY = None
if __name__ == '__main__' and len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
    LIBRARY = sys.argv.pop(1)


class ExecutionTests(unittest.TestCase, ExecutionFixture):
    library = LIBRARY
    setUp = ExecutionFixture.setUp

    def test_submission_is_not_fill_and_partial_then_full(self):
        e, _, _, _ = self.scenario(config=ExecutionConfig(fee_per_share=100))
        result = e.submit(Intent('buy', 'AAPL', 'buy', 15, 1_001_000))
        self.assertEqual(result.status, 'accepted')
        self.assertEqual(e.snapshot.account.cash, 100_000_000)
        self.assertEqual(e.snapshot.account.positions, ())
        self.assertEqual(e.snapshot.reserved_cash, 15_016_500)
        e.step()
        self.assertEqual(
            (e.snapshot.account.orders[-1].status, e.snapshot.account.orders[-1].filled),
            ('partial', 10),
        )
        self.assertEqual(e.snapshot.reserved_cash, 5_005_500)
        self.assertEqual(e.snapshot.account.cash, 89_989_000)
        e.step()
        self.assertEqual(e.last_fills, ())  # unchanged ask depth cannot be consumed twice
        e.step()
        self.assertEqual(e.snapshot.account.orders[-1].status, 'filled')
        self.assertEqual((e.snapshot.account.cash, e.snapshot.account.fees), (84_983_500, 1500))
        self.assertEqual(
            (e.snapshot.account.positions[0].quantity, e.snapshot.account.positions[0].cost),
            (15, 15_016_500),
        )
        self.assertEqual(e.snapshot.reserved_cash, 0)

    def test_fifo_and_shared_liquidity_budget(self):
        e, _, _, _ = self.scenario()
        first = e.submit(Intent('first', 'AAPL', 'buy', 7, 1_001_000))
        second = e.submit(Intent('second', 'AAPL', 'buy', 7, 1_001_000))
        e.step()
        orders = {o.order_id: o for o in e.snapshot.account.orders}
        self.assertEqual(orders[first.order_id].filled, 7)
        self.assertEqual(orders[second.order_id].filled, 3)
        self.assertEqual(sum(f['quantity'] for f in e.last_fills), 10)

    def test_cash_reservations_and_cancellation_release(self):
        e, _, _, _ = self.scenario(cash=2_000_000, config=ExecutionConfig(fee_per_share=100))
        first = e.submit(Intent('first', 'AAPL', 'buy', 1, 1_001_000))
        second = e.submit(Intent('second', 'AAPL', 'buy', 1, 1_001_000))
        self.assertEqual(second.status, 'rejected')
        self.assertIn('available cash', second.reason)
        e.cancel(first.order_id)
        self.assertEqual(e.snapshot.reserved_cash, 0)
        self.assertEqual(e.submit(Intent('third', 'AAPL', 'buy', 1, 1_001_000)).status, 'accepted')

    def test_fee_is_reserved_at_acceptance(self):
        e, _, _, _ = self.scenario(cash=1_001_000, config=ExecutionConfig(fee_per_share=1))
        result = e.submit(Intent('fee', 'AAPL', 'buy', 1, 1_001_000))
        self.assertEqual(result.status, 'rejected')
        self.assertEqual(e.snapshot.account.cash, 1_001_000)

    def test_latency_and_event_boundary(self):
        e, _, _, _ = self.scenario(
            tail=reduce('X', 1, 1, timestamp=4) + reduce('X', 1, 1, timestamp=10),
            config=ExecutionConfig(latency_ns=5),
        )
        e.submit(Intent('slow', 'AAPL', 'buy', 10, 1_001_000))
        e.step()
        self.assertEqual(e.last_fills, ())
        e.step()
        self.assertEqual(len(e.last_fills), 1)
        self.assertEqual(e.snapshot.time_ns, 10)
        events = self.app.events(self.profile, self.account)
        fill = next(x for x in events if x['kind'] == 'fill')
        self.assertGreaterEqual(fill['time_ns'], 8)
        self.assertGreater(fill['data']['market_sequence'], 3)

    def test_passive_limit_does_not_fill_from_trade_print(self):
        # Trade E at the bid does not establish a queue position for our passive order.
        e, _, _, _ = self.scenario(tail=reduce('E', 1, 1, timestamp=4))
        e.submit(Intent('passive', 'AAPL', 'buy', 10, 1_000_000))
        e.step()
        self.assertEqual(e.last_fills, ())
        self.assertEqual(e.snapshot.account.positions, ())

    def test_walk_multiple_prices_and_limit_cap(self):
        e, _, _, _ = self.scenario(
            tail=add(3, 'S', 4, 1_002_000, 'AAPL', timestamp=4) + reduce('X', 1, 1, timestamp=5),
            config=ExecutionConfig(fee_per_share=100),
        )
        e.step()
        e.submit(Intent('walk', 'AAPL', 'buy', 12, 1_002_000))
        e.step()
        self.assertEqual(
            [(f['price'], f['quantity']) for f in e.last_fills], [(1_001_000, 10), (1_002_000, 2)]
        )
        self.assertEqual(e.snapshot.account.cash, 87_984_800)
        self.assertEqual(e.snapshot.account.positions[0].cost, 12_015_200)

    def test_no_short_and_sell_share_reservations(self):
        e, _, _, _ = self.scenario()
        self.assertEqual(e.submit(Intent('short', 'AAPL', 'sell', 1, 1_000_000)).status, 'rejected')
        e.submit(Intent('buy', 'AAPL', 'buy', 10, 1_001_000))
        e.step()
        sell = e.submit(Intent('sell', 'AAPL', 'sell', 8, 1_000_000))
        self.assertEqual(e.snapshot.reserved_shares, (('AAPL', 8),))
        self.assertEqual(
            e.submit(Intent('oversell', 'AAPL', 'sell', 3, 1_000_000)).status, 'rejected'
        )
        e.cancel(sell.order_id)
        self.assertEqual(e.snapshot.reserved_shares, ())
        self.assertEqual(e.submit(Intent('new', 'AAPL', 'sell', 3, 1_000_000)).status, 'accepted')

    def test_cancel_partial_and_late_fill_rejected_by_ledger(self):
        e, _, run, _ = self.scenario()
        order = e.submit(Intent('partial', 'AAPL', 'buy', 15, 1_001_000))
        e.step()
        e.cancel(order.order_id)
        account = e.snapshot.account
        e.step()
        e.step()
        self.assertEqual(e.snapshot.account.cash, account.cash)
        self.assertEqual(e.snapshot.account.orders[-1].status, 'cancelled')
        records = self.app.events(self.profile, self.account)
        state = replay(LedgerEvent.from_record(r) for r in records)
        with self.assertRaisesRegex(ContractError, 'not open'):
            apply_event(
                state,
                LedgerEvent(
                    self.account,
                    state.sequence + 1,
                    'late',
                    run,
                    6,
                    'fill',
                    dict(order_id=order.order_id, price=1_001_000, quantity=1, fee=0),
                ),
            )
        self.assertEqual(state.cash, account.cash)

    def test_submit_and_cancel_retry_are_idempotent(self):
        e, _, _, _ = self.scenario()
        intent = Intent('same', 'AAPL', 'buy', 1, 1_001_000)
        first = e.submit(intent)
        before = e.snapshot
        self.assertEqual(e.submit(intent), first)
        self.assertEqual(e.snapshot, before)
        with self.assertRaises(ContractError):
            e.submit(Intent('same', 'AAPL', 'buy', 2, 1_001_000))
        e.cancel(first.order_id)
        before = e.snapshot
        e.cancel(first.order_id)
        self.assertEqual(e.snapshot, before)

    def test_risk_cannot_be_bypassed_by_account_entrypoints(self):
        e, _, run, _ = self.scenario()
        with self.assertRaisesRegex(ContractError, 'ExecutionSession'):
            self.app.decision_session(self.profile, run, self.maker).submit(
                Intent('bypass', 'AAPL', 'buy', 1, 1), 3
            )
        with self.assertRaisesRegex(ContractError, 'forbidden'):
            FixtureExecutor(self.app, self.profile, run).accept('fake', 'order', 3)
        with self.assertRaisesRegex(ContractError, 'ExecutionSession'):
            self.app.finish_run(self.profile, run, 'stopped')

    def test_quantity_and_symbol_rejections(self):
        e, _, _, _ = self.scenario(config=ExecutionConfig(max_order_quantity=5))
        self.assertEqual(
            e.submit(Intent('too-many', 'AAPL', 'buy', 6, 1_001_000)).status, 'rejected'
        )
        self.assertEqual(e.submit(Intent('other', 'MSFT', 'buy', 1, 1_001_000)).status, 'rejected')
        with self.assertRaises(ContractError):
            e.submit(Intent('invalid', 'AAPL', 'buy', 0, 1_001_000))

    def test_halt_blocks_acceptance_and_fills(self):
        halt = frame(common('H', timestamp=4) + b'AAPL    ' + b'H' + b' ' + b'T1  ')
        resume = frame(common('H', timestamp=6) + b'AAPL    ' + b'T' + b' ' + b'    ')
        e, _, _, _ = self.scenario(
            tail=halt + reduce('X', 1, 1, timestamp=5) + resume + reduce('X', 1, 1, timestamp=7)
        )
        e.submit(Intent('before-halt', 'AAPL', 'buy', 1, 1_001_000))
        e.step()
        self.assertEqual(e.submit(Intent('halted', 'AAPL', 'buy', 1, 1_001_000)).status, 'rejected')
        e.step()
        self.assertEqual(e.last_fills, ())
        e.step()
        self.assertEqual(e.last_fills, ())
        e.step()
        self.assertEqual(e.last_fills[0]['quantity'], 1)

    def test_end_cancels_unfilled_and_releases_reservations(self):
        e, _, _, _ = self.scenario(tail=b'')
        e.submit(Intent('unfilled', 'AAPL', 'buy', 1, 1_000_000))
        e.step()
        self.assertEqual(e.snapshot.status, 'completed')
        self.assertEqual(e.snapshot.account.orders[-1].status, 'cancelled')
        self.assertEqual(e.snapshot.reserved_cash, 0)
        with self.assertRaises(ContractError):
            e.submit(Intent('late', 'AAPL', 'buy', 1, 1))

    def test_no_book_rejects_new_order(self):
        source = self.path / 'initial.bin'
        source.write_bytes(self.prefix() + b'\0\0')
        m = self.app.open_market(library=LIBRARY)
        self.addCleanup(m.close)
        m.load(source)
        run = self.app.create_execution_run(self.profile, self.account, self.maker, 'AAPL')
        e = self.app.execution_session(self.profile, run, self.maker, m)
        self.addCleanup(e.close)
        result = e.submit(Intent('before-book', 'AAPL', 'buy', 1, 1_001_000))
        self.assertEqual(result.status, 'rejected')
        self.assertIn('no order book', result.reason)
        self.assertEqual(e.snapshot.account.cash, 100_000_000)

    def test_same_timestamp_still_requires_a_later_source_event(self):
        e, _, _, _ = self.scenario(tail=reduce('X', 1, 1, timestamp=3))
        e.submit(Intent('same-time', 'AAPL', 'buy', 1, 1_001_000))
        self.assertEqual(e.snapshot.account.positions, ())
        e.step()
        self.assertEqual(e.snapshot.time_ns, 3)
        self.assertEqual(e.last_fills[0]['market_sequence'], 4)

    def test_price_improvement_releases_cash_reservation(self):
        e, _, _, _ = self.scenario(config=ExecutionConfig(fee_per_share=100))
        e.submit(Intent('improved', 'AAPL', 'buy', 5, 1_100_000))
        self.assertEqual(e.snapshot.reserved_cash, 5_500_500)
        e.step()
        self.assertEqual(e.snapshot.reserved_cash, 0)
        self.assertEqual(e.snapshot.account.cash, 94_994_500)

    def test_pause_does_not_fill_or_advance_time(self):
        e, _, _, _ = self.scenario()
        e.submit(Intent('paused', 'AAPL', 'buy', 1, 1_001_000))
        before = e.snapshot
        e.play()
        e.pause()
        self.assertEqual(e.pump(5), ())
        self.assertEqual(e.snapshot, before)
        e.play()
        e.pump(1)
        self.assertEqual(e.snapshot.account.orders[-1].filled, 1)

    def test_news_does_not_trigger_fills(self):
        e, _, _, _ = self.scenario(news=[NewsRecord('report', 4, '2026-Q1', 'published')])
        e.submit(Intent('news', 'AAPL', 'buy', 1, 1_001_000))
        self.assertEqual(e.step().kind, 'news')
        self.assertEqual(e.last_fills, ())
        self.assertEqual(e.snapshot.market_sequence, 3)
        self.assertEqual(e.step().kind, 'market')
        self.assertEqual(e.last_fills[0]['quantity'], 1)

    def test_gap_fails_run_and_cancels_orders(self):
        source = self.prefix()
        records = []
        offset = 0
        while offset < len(source):
            size = int.from_bytes(source[offset : offset + 2], 'big')
            records.append(FeedRecord(len(records) + 1, source[offset + 2 : offset + 2 + size]))
            offset += 2 + size
        records.append(FeedRecord(5, reduce('X', 1, 1, timestamp=4)[2:]))
        m = self.app.open_market(library=LIBRARY)
        self.addCleanup(m.close)
        m.load_stream(SequencedFeed(records))
        run = self.app.create_execution_run(self.profile, self.account, self.maker, 'AAPL')
        e = self.app.execution_session(self.profile, run, self.maker, m)
        self.addCleanup(e.close)
        for _ in range(3):
            e.step()
        e.submit(Intent('gap', 'AAPL', 'buy', 1, 1_001_000))
        self.assertEqual(e.step().kind, 'integrity')
        self.assertEqual(e.snapshot.status, 'failed')
        self.assertEqual(e.snapshot.account.orders[-1].status, 'cancelled')
        self.assertEqual(e.snapshot.account.positions, ())

    def test_acceptance_storage_failure_rolls_back_intent_and_reservation(self):
        e, _, _, _ = self.scenario()
        before = e.snapshot
        original = RunService.record_run_event

        def failed(service, run, event_id, time, kind, data):
            if kind == 'accepted':
                raise OSError('injected acceptance failure')
            return original(service, run, event_id, time, kind, data)

        with patch.object(RunService, 'record_run_event', failed), self.assertRaises(OSError):
            e.submit(Intent('atomic', 'AAPL', 'buy', 1, 1_001_000))
        self.assertEqual(e.snapshot, before)
        self.assertEqual(e.submit(Intent('atomic', 'AAPL', 'buy', 1, 1_001_000)).status, 'accepted')

    def test_fill_and_liquidity_storage_failure_roll_back_together(self):
        e, m, _, _ = self.scenario()
        e.submit(Intent('atomic-fill', 'AAPL', 'buy', 1, 1_001_000))
        original = RunService.record_run_event

        def failed(service, run, event_id, time, kind, data):
            if kind == 'fill':
                raise OSError('injected fill failure')
            return original(service, run, event_id, time, kind, data)

        with patch.object(RunService, 'record_run_event', failed), self.assertRaises(OSError):
            e.step()
        self.assertEqual(e.snapshot.status, 'failed')
        self.assertEqual(e.snapshot.account.cash, 100_000_000)
        self.assertEqual(e.snapshot.account.positions, ())
        self.assertEqual(e.snapshot.account.orders[-1].status, 'cancelled')
        self.assertFalse(
            any(r['kind'] == 'fill' for r in self.app.events(self.profile, self.account))
        )
        self.assertEqual(m.status, 'closed')

    def test_external_market_advancement_stops_execution(self):
        e, m, _, _ = self.scenario()
        m.step()
        with self.assertRaisesRegex(ContractError, 'outside'):
            e.submit(Intent('bad-time', 'AAPL', 'buy', 1, 1_001_000))
        self.assertEqual(e.snapshot.status, 'failed')

    def test_concurrent_cash_reservations_are_serialized(self):
        e, _, _, _ = self.scenario(cash=2_000_000)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda client: e.submit(Intent(client, 'AAPL', 'buy', 1, 1_001_000)), ['a', 'b']
                )
            )
        self.assertEqual(sorted(r.status for r in results), ['accepted', 'rejected'])
        self.assertGreaterEqual(e.snapshot.available_cash, 0)

    def test_account_reopen_and_ledger_replay(self):
        e, _, run, account = self.scenario()
        e.submit(Intent('persisted', 'AAPL', 'buy', 15, 1_001_000))
        self.drain(e)
        expected = e.snapshot.account
        events = self.app.events(self.profile, account)
        self.assertEqual(self.app.replay_snapshot(self.profile, account, events), expected)
        with Platform(self.path / 'accounts') as reopened:
            self.assertEqual(reopened.snapshot(self.profile, account), expected)
            self.assertEqual(reopened.run(self.profile, run)['status'], 'completed')

    def test_repeat_scenario_has_identical_normalized_results(self):
        def run_once():
            e, _, run, account = self.scenario(
                cash=100_000_000, config=ExecutionConfig(fee_per_share=100)
            )
            e.submit(Intent('same', 'AAPL', 'buy', 15, 1_001_000))
            self.drain(e)
            events = self.app.events(self.profile, account)
            return (
                e.snapshot.account.cash,
                e.snapshot.account.positions,
                e.snapshot.account.fees,
                [
                    (
                        r['kind'],
                        r['time_ns'],
                        {
                            k: v
                            for k, v in r['data'].items()
                            if k in ('price', 'quantity', 'fee', 'market_sequence', 'tick')
                        },
                    )
                    for r in events
                ],
            )

        self.assertEqual(run_once(), run_once())

    def test_execution_owner_and_restart_refused(self):
        e, _, run, _ = self.scenario()
        m = self.app.open_market(library=LIBRARY)
        self.addCleanup(m.close)
        path = self.path / 'new.bin'
        path.write_bytes(self.prefix() + b'\0\0')
        m.load(path)
        with Platform(self.path / 'accounts') as second:
            with self.assertRaises(ContractError):
                second.execution_session(self.profile, run, self.maker, m)

    def test_duplicate_market_event_cannot_fill_twice(self):
        e, _, run, _ = self.scenario()
        e.submit(Intent('once', 'AAPL', 'buy', 1, 1_001_000))
        event = e.step()
        before = e.snapshot
        with self.assertRaisesRegex(ContractError, 'sequence gap'):
            self.app._operations.execution('advance', self.profile, run, event, write=True)
        self.assertEqual(e.snapshot, before)

    def test_explicit_recovery_stop_releases_persisted_orders(self):
        e, _, run, _ = self.scenario()
        e.submit(Intent('recover', 'AAPL', 'buy', 1, 1_001_000))
        with self.assertRaises(ContractError):
            self.app.abort_execution_run(self.profile, run)
        # Release process ownership to simulate an actual dead owner. A live owner's
        # account lease must prevent another instance from performing recovery.
        e._unbind()
        # A newly opened app represents explicit recovery after the previous owner died.
        with Platform(self.path / 'accounts') as reopened:
            result = reopened.abort_execution_run(self.profile, run)
            self.assertEqual(result.status, 'stopped')
            self.assertEqual(result.reserved_cash, 0)
            self.assertEqual(result.account.orders[-1].status, 'cancelled')
        self.assertTrue(e.closed)

    def test_new_run_can_reuse_client_id_without_order_id_collision(self):
        e, _, _, _ = self.scenario(tail=b'')
        first = e.submit(Intent('same', 'AAPL', 'buy', 1, 1_000_000))
        e.step()
        second, _, _, _ = self.scenario(tail=b'')
        later = second.submit(Intent('same', 'AAPL', 'buy', 1, 1_000_000))
        self.assertEqual(later.status, 'accepted')
        self.assertNotEqual(first.order_id, later.order_id)

    def test_cli_market_queries_match_market_without_advancing_trading_state(self):
        source = self.path / 'queries.bin'
        source.write_bytes(
            self.prefix()
            + reduce('E', 2, 4, timestamp=4)
            + reduce('X', 1, 1, timestamp=5)
            + b'\0\0'
        )
        news = self.path / 'news.json'
        news.write_text(
            json.dumps(
                [
                    dict(
                        record_id='visible',
                        available_time_ns=4,
                        period='fixture',
                        headline='Visible',
                    ),
                    dict(
                        record_id='future', available_time_ns=9, period='fixture', headline='Future'
                    ),
                ]
            )
        )
        common = ['--input', str(source), '--news', str(news)]
        if LIBRARY:
            common += ['--library', LIBRARY]
        market = subprocess.run(
            [
                sys.executable,
                '-m',
                'sim_platform',
                'market',
                *common,
                '--commands',
                'step 5;book;trades;bars;news;quit',
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(market.returncode, 0, market.stderr)
        expected = [json.loads(line) for line in market.stdout.splitlines()][-4:]
        self.assertEqual(expected[0]['asks'], [[1_001_000, 6]])
        self.assertEqual(expected[1][0]['quantity'], 4)
        self.assertEqual(expected[2][0]['volume'], 4)
        self.assertEqual([item['record_id'] for item in expected[3]], ['visible'])

        script = (
            'step 5;buy cli 2 100.10;events;snapshot;book;trades;bars;news;'
            'snapshot;events;step;snapshot;help;quit'
        )
        for mode in ('execution', 'decision'):
            for interactive in (False, True):
                with self.subTest(mode=mode, interactive=interactive):
                    account = self.app.create_account(self.profile, 'Queries', 100_000_000)
                    command = [
                        sys.executable,
                        '-m',
                        'sim_platform',
                        '--data-dir',
                        str(self.path / 'accounts'),
                        mode,
                        '--profile',
                        self.profile,
                        '--account',
                        account,
                        '--maker',
                        self.maker,
                        *common,
                    ]
                    if mode == 'decision':
                        command += ['--policy', 'human']
                    if not interactive:
                        command += ['--commands', script]
                    result = subprocess.run(
                        command,
                        cwd=ROOT,
                        text=True,
                        capture_output=True,
                        input=script.replace(';', '\n') + '\n' if interactive else None,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('book, trades, bars, news', result.stderr)
                    self.assertEqual('handoff MAKER' in result.stderr, mode == 'decision')
                    output = [json.loads(line) for line in result.stdout.splitlines()]
                    snapshots = [
                        row
                        for row in output
                        if isinstance(row, dict) and 'market' in row and 'execution' in row
                    ]
                    before, after, filled, final = snapshots
                    self.assertEqual(before, after)
                    index = output.index(before)
                    self.assertEqual(output[index + 1 : index + 5], expected)
                    # Queries must not create ledger events, consume liquidity,
                    # advance time, or change pending orders/reservations.
                    self.assertEqual(output[index - 1], output[index + 6])
                    self.assertEqual(before['execution']['account']['positions'], [])
                    self.assertEqual(before['execution']['account']['orders'][0]['filled'], 0)
                    self.assertEqual(filled['execution']['account']['positions'][0]['quantity'], 2)
                    self.assertEqual(filled['execution']['account']['cash'], 97_998_000)
                    self.assertEqual(final['execution']['status'], 'stopped')

    def test_cli_invalid_session_commands_close_run_and_cancel_pending_order(self):
        source = self.path / 'invalid-command.bin'
        source.write_bytes(self.prefix() + b'\0\0')
        for invalid, message in (
            ('book extra', 'book takes no arguments'),
            ('step 0', 'count must be 1..1000000'),
            ('unknown', 'unknown command: unknown'),
            ('quit extra', 'quit takes no arguments'),
        ):
            with self.subTest(command=invalid):
                account = self.app.create_account(self.profile, 'Invalid input', 100_000_000)
                command = [
                    sys.executable,
                    '-m',
                    'sim_platform',
                    '--data-dir',
                    str(self.path / 'accounts'),
                    'execution',
                    '--profile',
                    self.profile,
                    '--account',
                    account,
                    '--maker',
                    self.maker,
                    '--input',
                    str(source),
                    '--commands',
                    f'step 3;buy cli 2 100.10;{invalid}',
                ]
                if LIBRARY:
                    command += ['--library', LIBRARY]
                result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                run_id = json.loads(result.stdout.splitlines()[0])['run_id']
                self.assertEqual(self.app.run(self.profile, run_id)['status'], 'stopped')
                snapshot = self.app.snapshot(self.profile, account)
                self.assertEqual(snapshot.cash, 100_000_000)
                self.assertEqual(snapshot.orders[0].status, 'cancelled')
                self.assertEqual(snapshot.orders[0].filled, 0)

    def test_demo_and_cli_execution(self):
        report = run_execution_demo()
        self.assertEqual(report['result'], 'passed')
        self.assertEqual(report['final']['account']['cash'], 42_943_000)
        source = self.path / 'cli.bin'
        source.write_bytes(self.prefix() + reduce('X', 1, 1, timestamp=4) + b'\0\0')
        command = [
            sys.executable,
            '-m',
            'sim_platform',
            '--data-dir',
            str(self.path / 'accounts'),
            'execution',
            '--profile',
            self.profile,
            '--account',
            self.account,
            '--maker',
            self.maker,
            '--input',
            str(source),
            '--fee-per-share',
            '0.01',
            '--commands',
            'step 3;buy cli 5 100.10;snapshot;play;tick 1;pause;snapshot;run;events',
        ]
        if LIBRARY:
            command += ['--library', LIBRARY]
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(lines[-1]['execution']['status'], 'completed')
        self.assertEqual(lines[-1]['execution']['account']['cash'], 94_994_500)
        self.assertEqual(lines[-1]['execution']['account']['positions'][0]['quantity'], 5)


if __name__ == '__main__':
    unittest.main()
