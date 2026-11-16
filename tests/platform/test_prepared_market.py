"""Independent price/sequence expectations and real native/UOW probes."""

from support.financial import assemble_probe
from support.book import PreparedBookFixture, fixture

from dataclasses import asdict, replace
from pathlib import Path
from threading import Event, Thread
import struct
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from itch_fixtures import directory, add, reduce, common, frame
from sim_platform import Platform, Intent, ContractError
from sim_platform.market.session import MarketSession
from sim_platform.market.models import FutureDataError, DataUnavailable
from sim_platform.market.prepared.builder import build, PreparationCancelled
from sim_platform.market.prepared.reader import PreparedMarket, VisibleMarket
from sim_platform.market.prepared.job import PreparationJob
from sim_platform.experiments.execution.timeline.sequencer import Sequencer
from sim_platform.execution.models import ExecutionConfig


class PreparedMarketTests(PreparedBookFixture, unittest.TestCase):

    def test_asof_is_direct_version_lookup_with_equal_timestamp_prefix(self):
        reader = self.ready()
        with patch(
            'sim_platform.market.native.NativeEngine',
            side_effect=AssertionError('no reconstruction'),
        ):
            self.assertEqual(reader.snapshot_at(0).sequence, 0)
            self.assertEqual(reader.snapshot_at(3).asks, ((101, 2),))
            self.assertEqual(reader.snapshot_at(9).sequence, 3)
            self.assertEqual(reader.snapshot_at(10).sequence, 5)
            self.assertEqual(reader.snapshot_at(10).asks, ((101, 2), (102, 3), (103, 4)))
            self.assertEqual(reader.snapshot_at(29).asks, ((101, 2), (102, 3), (103, 4)))
            self.assertEqual(reader.snapshot_at(30).asks[0], (101, 12))
        self.assertEqual(reader.manifest['frames'], 7)

    def test_visible_query_rejects_future_and_fixes_delivery_cutoff(self):
        reader = self.ready()
        now = [9]
        visible = VisibleMarket(reader, lambda: now[0])
        captured = visible.capture()
        now[0] = 30
        self.assertEqual(captured['prefix'], 3)
        self.assertEqual(captured['snapshot'].asks, ((101, 2),))
        self.assertEqual(visible.capture(10)['prefix'], 5)
        now[0] = 32
        with self.assertRaises(DataUnavailable):
            visible.capture(31)
        now[0] = 30
        with self.assertRaises(FutureDataError):
            visible.capture(31)

    def test_each_event_matches_original_native_session(self):
        reader = self.ready()
        market = MarketSession('AAPL')
        self.addCleanup(market.close)
        market.load(self.source)
        market.play()
        for index in range(reader.manifest['frames']):
            self.assertEqual(asdict(reader.event(index)), asdict(market.pump(1)[0]))

    def test_later_broken_trade_does_not_rewrite_earlier_event(self):
        trade = frame(
            common('P', timestamp=4)
            + struct.pack('>Q', 55)
            + b'B'
            + struct.pack('>I', 2)
            + b'AAPL    '
            + struct.pack('>I', 101)
            + struct.pack('>Q', 77)
        )
        self.source.write_bytes(
            directory('AAPL', timestamp=1)
            + add(1, 'S', 4, 101, 'AAPL', timestamp=2)
            + trade
            + frame(common('B', timestamp=8) + struct.pack('>Q', 77))
            + b'\0\0'
        )
        reader = self.ready()
        self.assertEqual(reader.event(2).trade.match_id, 77)
        self.assertIsNone(reader.event(2).broken_match_id)
        self.assertEqual(reader.event(3).broken_match_id, 77)
        self.assertEqual(reader.snapshot_at(7).sequence, 3)

    def test_invalid_source_cancel_and_existing_destination_never_publish(self):
        self.source.write_bytes(self.source.read_bytes()[:-1])
        with self.assertRaises(ContractError):
            build(self.source, self.destination)
        self.assertFalse(self.destination.exists())
        fixture(self.source)
        with self.assertRaises(PreparationCancelled):
            build(self.source, self.destination, cancelled=lambda: True)
        self.assertFalse(self.destination.exists())
        self.ready()
        original = (self.destination / 'manifest.json').read_bytes()
        with self.assertRaises(ContractError):
            build(self.source, self.destination)
        self.assertEqual((self.destination / 'manifest.json').read_bytes(), original)
        self.assertEqual(list(self.root.glob('.prepared-*')), [])

    def test_corrupt_length_and_nonmonotone_index_are_rejected(self):
        reader = self.ready()
        reader.close()
        path = self.destination / 'events.bin'
        data = path.read_bytes()
        path.write_bytes(data[:-1])
        with self.assertRaises(ContractError):
            PreparedMarket(self.destination)
        path.write_bytes(data[:0] + (99).to_bytes(8, 'big') + data[8:])
        with self.assertRaises(ContractError):
            PreparedMarket(self.destination)

    def test_wrong_symbol_and_backwards_time_reject_before_ready(self):
        with self.assertRaises(ContractError):
            build(self.source, self.destination, symbol='ZZZZ')
        self.assertFalse(self.destination.exists())
        self.source.write_bytes(
            directory('AAPL', timestamp=10) + add(1, 'S', 1, 101, 'AAPL', timestamp=9) + b'\0\0'
        )
        with self.assertRaises(ContractError):
            build(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_cancel_spawn_cleans_only_owned_staging(self):
        source = self.root / 'large.bin'
        with source.open('wb') as stream:
            stream.write(directory('AAPL', timestamp=1))
            for index in range(10000):
                stream.write(add(index + 1, 'S', 1, 101, 'AAPL', timestamp=index + 2))
            stream.write(b'\0\0')
        job = PreparationJob(source, self.destination)
        self.addCleanup(job.close)
        job.cancel()
        with self.assertRaises(ContractError):
            job.result(10)
        job.close()
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.root.glob('.preparation-job-*')), [])

    def test_spawn_preparation_returns_ready_artifact_and_reaps(self):
        job = PreparationJob(self.source, self.destination)
        self.addCleanup(job.close)
        self.assertEqual(job.result(10), self.destination.resolve())
        job.close()
        self.assertFalse(job.snapshot()['alive'])
        self.assertEqual(job.snapshot()['frames'], 7)


class PreparedImportTests(unittest.TestCase):
    setUp = PreparedBookFixture.setUp
    ready = PreparedBookFixture.ready

    def test_foreign_events_are_scanned_but_not_stored_and_raw_sequence_survives(self):
        self.source.write_bytes(
            directory('MSFT', locate=8, timestamp=1)
            + directory('AAPL', timestamp=2)
            + directory('MSFT', locate=8, timestamp=3)
            + add(1, 'S', 4, 101, 'AAPL', timestamp=4)
            + directory('MSFT', locate=8, timestamp=4)
            + reduce('X', 1, 1, timestamp=4)
            + directory('MSFT', locate=8, timestamp=9)
            + b'\0\0'
        )
        reader = self.ready()
        self.assertEqual(reader.manifest['source_frames'], 7)
        self.assertEqual(reader.manifest['frames'], 3)
        self.assertEqual(reader.manifest['last_ns'], 9)
        self.assertEqual(reader.prefix(4), 3)
        self.assertEqual([reader.source_sequence(i) for i in range(4)], [0, 2, 4, 6])
        self.assertEqual(reader.snapshot_at(8).sequence, 3)
        self.assertEqual(reader.snapshot_at(8).asks, ((101, 3),))
        captured = VisibleMarket(reader, lambda: 9).capture()
        self.assertEqual(captured['source_time_ns'], 4)
        self.assertEqual(captured['source_sequence'], 6)
        self.assertEqual(captured['target_ns'], 9)
        market = MarketSession('AAPL')
        self.addCleanup(market.close)
        market.load(self.source)
        market.play()
        index = 0
        while market.status == 'playing':
            event = market.pump(1)[0]
            if event.kind != 'market':
                continue
            actual = reader.event(index)
            expected = replace(
                event, sequence=index + 1, snapshot=replace(event.snapshot, sequence=index + 1)
            )
            self.assertEqual(asdict(actual), asdict(expected))
            index += 1
        self.assertEqual(index, 3)

    def test_skipped_messages_still_reject_bad_lengths_and_backwards_time(self):
        for bad in (
            frame(common('R', locate=8, timestamp=4)),
            directory('MSFT', locate=8, timestamp=1),
        ):
            self.source.write_bytes(directory('AAPL', timestamp=2) + bad + b'\0\0')
            with self.assertRaises(ContractError):
                build(self.source, self.destination)
            self.assertFalse(self.destination.exists())

    def test_implicit_eof_system_halt_and_trade_metadata_keep_shared_semantics(self):
        trade = frame(
            common('P', timestamp=5)
            + struct.pack('>Q', 55)
            + b'B'
            + struct.pack('>I', 2)
            + b'AAPL    '
            + struct.pack('>I', 101)
            + struct.pack('>Q', 77)
        )
        self.source.write_bytes(
            directory('AAPL', timestamp=1)
            + add(1, 'S', 4, 101, 'AAPL', timestamp=2)
            + frame(common('H', timestamp=3) + b'AAPL    ' + b'H' + b' ' + b'    ')
            + trade
            + frame(common('S', locate=0, timestamp=8) + b'C')
        )
        build(self.source, self.destination, allow_implicit_eof=True)
        with PreparedMarket(self.destination, verify=True) as reader:
            self.assertEqual(reader.event(2).snapshot.trading_state, 'H')
            self.assertEqual(reader.event(3).trade.match_id, 77)
            self.assertEqual(reader.event(4).snapshot.system_event, 'C')
            self.assertEqual(reader.manifest['source_frames'], 5)
        with self.assertRaises(ContractError):
            build(self.source, self.root / 'strict')

    def test_normal_open_does_not_rescan_but_explicit_validation_checks_middle(self):
        reader = self.ready()
        reader.close()
        with patch.object(PreparedMarket, 'validate', side_effect=AssertionError('no scan')):
            with PreparedMarket(self.destination) as reopened:
                self.assertEqual(reopened.snapshot_at(10).sequence, 5)
        import json

        event_path = self.destination / 'events.bin'
        data = bytearray(event_path.read_bytes())
        from sim_platform.market.prepared.codec import EVENT

        data[3 * EVENT.size : 3 * EVENT.size + 8] = (1).to_bytes(8, 'big')
        event_path.write_bytes(data)
        # Metadata changes are rejected even before full validation.
        with self.assertRaises(ContractError):
            PreparedMarket(self.destination)
        manifest_path = self.destination / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        stat = event_path.stat()
        manifest['files']['events.bin'] = dict(bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(ContractError):
            PreparedMarket(self.destination, verify=True)

    def test_old_format_requires_rebuild_and_never_silently_reinterprets_sequence(self):
        reader = self.ready()
        reader.close()
        import json

        path = self.destination / 'manifest.json'
        value = json.loads(path.read_text())
        value['version'] = 'prepared-market/v1'
        path.write_text(json.dumps(value))
        with self.assertRaises(ContractError):
            PreparedMarket(self.destination)

    def test_cancel_after_native_batch_and_source_mutation_do_not_publish(self):
        with self.source.open('wb') as stream:
            stream.write(directory('AAPL', timestamp=1))
            for index in range(20000):
                stream.write(directory('MSFT', locate=8, timestamp=index + 2))
            stream.write(b'\0\0')
        progress = []
        with self.assertRaises(PreparationCancelled):
            build(
                self.source,
                self.destination,
                progress=progress.append,
                cancelled=lambda: bool(progress),
            )
        self.assertEqual(progress, [8192])
        self.assertFalse(self.destination.exists())
        fixture(self.source)

        def mutate(_):
            with self.source.open('ab') as stream:
                stream.write(b'x')

        with self.assertRaises(ContractError):
            build(self.source, self.destination, progress=mutate)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.root.glob('.prepared-*')), [])

    def test_optional_native_stage_timings_and_output_failure_are_explicit(self):
        timings = {}
        build(self.source, self.destination, timings=timings, profile_native=True)
        self.assertGreater(timings['native_read_ns'], 0)
        self.assertGreater(timings['native_apply_ns'], 0)
        self.assertGreater(timings['native_encode_write_ns'], 0)
        from sim_platform.market.prepared.native import NativePreparation

        with self.assertRaises(ContractError):
            NativePreparation(self.source, self.root / 'missing', 'AAPL', 5, None, False, False)


class SparsePreparedFinanceTests(unittest.TestCase):
    setUp = PreparedBookFixture.setUp

    def test_sparse_timeline_uses_original_finance_without_sequence_gaps_or_early_fills(self):
        self.source.write_bytes(
            directory('AAPL', timestamp=1)
            + add(1, 'S', 2, 101, 'AAPL', timestamp=2)
            + directory('MSFT', locate=8, timestamp=4)
            + directory('MSFT', locate=8, timestamp=10)
            + add(2, 'S', 10, 101, 'AAPL', timestamp=20)
            + directory('MSFT', locate=8, timestamp=30)
            + b'\0\0'
        )
        build(self.source, self.destination)
        with PreparedMarket(self.destination) as reader, Platform(self.root / 'accounts') as app:
            profile = app.create_profile('sparse')
            account = app.create_account(profile, 'cash', 10000)
            maker = app.create_maker(profile, 'human', 'human', 'v1')
            now = [4]
            probe = assemble_probe(
                app._operations,
                app._new_execution,
                profile,
                account,
                maker,
                reader,
                lambda: now[0],
                start_ns=2,
                end_ns=31,
                config=ExecutionConfig(latency_ns=10),
            )
            self.addCleanup(probe.close)
            probe.submit('buy', Intent('buy', 'AAPL', 'buy', 5, 101))
            now[0] = 15
            probe.turn()
            self.assertEqual(probe.execution.snapshot.account.orders[0].filled, 0)
            now[0] = 21
            probe.turn()
            self.assertEqual(probe.execution.snapshot.account.orders[0].filled, 5)
            self.assertEqual(probe.execution.snapshot.account.cash, 9495)
            self.assertEqual(probe.execution.snapshot.reserved_cash, 0)
            native_account = app.create_account(profile, 'native', 10000)
            run = app.create_execution_run(
                profile, native_account, maker, 'AAPL', config=ExecutionConfig(latency_ns=10)
            )
            market = app.open_market('AAPL')
            self.addCleanup(market.close)
            market.load(self.source)
            session = app.execution_session(profile, run, maker, market)
            self.addCleanup(session.close)
            for _ in range(3):
                session.step()
            session.submit(Intent('buy', 'AAPL', 'buy', 5, 101))
            for _ in range(3):
                session.step()
            self.assertEqual(session.snapshot.account.cash, probe.execution.snapshot.account.cash)
            self.assertEqual(session.snapshot.account.orders[0].filled, 5)
            session.close()
            probe.close()


class SequencerTests(unittest.TestCase):
    def test_equal_tick_admission_after_claim_is_not_backdated(self):
        now = [10]
        seq = Sequencer(lambda: now[0], start_ns=0, end_ns=100)
        batch = seq.claim()
        command = seq.admit('one', {'kind': 'test'})
        self.assertEqual(command.time_ns, batch.until_ns)
        self.assertNotIn(command, batch.commands)
        seq.release(batch)
        self.assertEqual(seq.claim().commands, ())
        seq.release(seq._batch)
        now[0] = 11
        self.assertEqual(seq.claim().commands, (command,))

    def test_stamp_and_append_cannot_be_overtaken_by_execution_claim(self):
        entered, release, claimed = Event(), Event(), Event()
        calls = [0]

        def clock():
            calls[0] += 1
            if calls[0] == 1:
                entered.set()
                if not release.wait(2):
                    raise RuntimeError('test release missing')
                return 10
            return 11

        seq = Sequencer(clock, start_ns=0, end_ns=100)
        admitted, batches = [], []
        producer = Thread(target=lambda: admitted.append(seq.admit('one', {})))
        consumer = Thread(target=lambda: (batches.append(seq.claim()), claimed.set()))
        producer.start()
        self.assertTrue(entered.wait(1))
        consumer.start()
        self.assertFalse(claimed.wait(0.03))
        release.set()
        producer.join(2)
        consumer.join(2)
        self.assertFalse(producer.is_alive() or consumer.is_alive())
        self.assertEqual(batches[0].commands, tuple(admitted))

    def test_multiple_producers_and_owner_never_cross_sealed_prefix(self):
        from time import monotonic_ns

        origin = monotonic_ns()
        seq = Sequencer(
            lambda: monotonic_ns() - origin,
            start_ns=0,
            end_ns=10_000_000_000,
            capacity=2000,
            retained=2000,
        )
        errors = []
        processed = []

        def produce(number):
            try:
                for index in range(300):
                    seq.admit(str(number) + '-' + str(index), {'n': number, 'i': index})
            except Exception as exc:
                errors.append(exc)

        producers = [Thread(target=produce, args=(number,)) for number in range(4)]
        for thread in producers:
            thread.start()
        while any(thread.is_alive() for thread in producers) or seq.snapshot()['pending']:
            batch = seq.claim()
            for command in batch.commands:
                self.assertLess(command.time_ns, batch.until_ns)
                seq.begin(command)
                seq.complete(command, {'ok': True})
                processed.append(command)
            seq.release(batch)
        for thread in producers:
            thread.join(1)
        self.assertEqual(errors, [])
        self.assertEqual(len(processed), 1200)
        self.assertEqual([c.sequence for c in processed], list(range(1, 1201)))
        self.assertEqual([c.time_ns for c in processed], sorted(c.time_ns for c in processed))

    def test_capacity_duplicate_conflict_and_end_pending(self):
        now = [10]
        seq = Sequencer(lambda: now[0], start_ns=0, end_ns=20, capacity=1)
        command = seq.admit('one', {'value': 1})
        self.assertEqual(seq.admit('one', {'value': 1}), command)
        with self.assertRaises(ContractError):
            seq.admit('one', {'value': 2})
        with self.assertRaises(ContractError):
            seq.admit('two', {})
        now[0] = 20
        self.assertIsNone(seq.claim())
        self.assertEqual(seq.result('one'), dict(status='cancelled', reason='end'))

    def test_stop_cancels_unstarted_but_started_commit_may_complete(self):
        now = [10]
        seq = Sequencer(lambda: now[0], start_ns=0, end_ns=100)
        one = seq.admit('one', {})
        two = seq.admit('two', {})
        now[0] = 11
        batch = seq.claim()
        self.assertTrue(seq.begin(one))
        seq.stop()
        seq.complete(one, {'status': 'accepted'})
        self.assertFalse(seq.begin(two))
        self.assertEqual(seq.result('one')['status'], 'accepted')
        self.assertEqual(seq.result('two')['status'], 'cancelled')
        seq.release(batch)


class PreparedFinanceTests(unittest.TestCase):
    setUp = PreparedBookFixture.setUp
    ready = PreparedBookFixture.ready

    def probe(self, *, cash=10000, config=None):
        reader = self.ready()
        app = Platform(self.root / 'accounts')
        self.addCleanup(app.close)
        profile = app.create_profile('probe')
        account = app.create_account(profile, 'cash', cash)
        maker = app.create_maker(profile, 'human', 'human', 'v1')
        now = [4]
        probe = assemble_probe(
            app._operations,
            app._new_execution,
            profile,
            account,
            maker,
            reader,
            lambda: now[0],
            start_ns=3,
            end_ns=31,
            config=config,
        )
        self.addCleanup(probe.close)
        return app, profile, account, probe, now

    def test_partial_fill_then_cancel_and_same_id_replay(self):
        app, profile, account, probe, now = self.probe()
        command = probe.submit('r1', Intent('buy', 'AAPL', 'buy', 5, 101))
        now[0] = 11
        probe.turn()
        first = probe.sequencer.result('r1')
        self.assertEqual(first['status'], 'accepted')
        snapshot = probe.execution.snapshot
        self.assertEqual(snapshot.account.orders[0].filled, 2)
        self.assertEqual(snapshot.account.cash, 9798)
        self.assertEqual(snapshot.reserved_cash, 303)
        self.assertEqual(probe.timeline.trace[0]['source_prefix'], 3)
        self.assertEqual(command.time_ns, 4)
        self.assertEqual(probe.submit('r1', Intent('buy', 'AAPL', 'buy', 5, 101)), command)
        now[0] = 15
        probe.cancel('r2', first['order_id'])
        now[0] = 21
        probe.turn()
        now[0] = 30
        probe.turn()
        self.assertEqual(probe.execution.snapshot.account.orders[0].filled, 2)
        self.assertEqual(probe.execution.snapshot.account.orders[0].status, 'cancelled')
        self.assertEqual(probe.execution.snapshot.reserved_cash, 0)
        self.assertEqual(len(app.snapshot(profile, account).orders), 1)

    def test_receipt_after_price_change_and_no_repeated_liquidity(self):
        _, _, _, probe, now = self.probe()
        now[0] = 10
        probe.submit('a', Intent('a', 'AAPL', 'buy', 2, 101))
        probe.submit('b', Intent('b', 'AAPL', 'buy', 2, 101))
        now[0] = 21
        probe.turn()
        orders = probe.execution.snapshot.account.orders
        by_id = {o.order_id: o.filled for o in orders}
        self.assertEqual(by_id[probe.sequencer.result('a')['order_id']], 2)
        self.assertEqual(by_id[probe.sequencer.result('b')['order_id']], 0)
        self.assertEqual(probe.timeline.trace[0]['source_prefix'], 5)
        self.assertEqual(probe.timeline.trace[1]['source_prefix'], 5)

    def test_prepared_finance_matches_native_with_same_effective_boundary(self):
        app, profile, account, probe, now = self.probe()
        now[0] = 3
        # Sequencer clock has not been sampled yet; start includes exactly prefix 3.
        probe.submit('buy', Intent('buy', 'AAPL', 'buy', 5, 101))
        now[0] = 21
        probe.turn()
        expected = probe.execution.snapshot
        other = app.create_account(profile, 'native', 10000)
        maker = app.create_maker(profile, 'native', 'human', 'v1')
        market = app.open_market('AAPL')
        self.addCleanup(market.close)
        market.load(self.source)
        run = app.create_execution_run(profile, other, maker, 'AAPL')
        session = app.execution_session(profile, run, maker, market)
        self.addCleanup(session.close)
        for _ in range(3):
            session.step()
        session.submit(Intent('buy', 'AAPL', 'buy', 5, 101))
        for _ in range(3):
            session.step()
        actual = session.snapshot
        self.assertEqual(actual.account.cash, expected.account.cash)
        self.assertEqual(actual.reserved_cash, expected.reserved_cash)
        self.assertEqual(actual.account.positions, expected.account.positions)
        self.assertEqual(actual.account.orders[0].filled, expected.account.orders[0].filled)

    def test_sell_reservation_still_uses_original_financial_rules(self):
        app, profile, account, probe, now = self.probe()
        probe.submit('buy', Intent('buy', 'AAPL', 'buy', 5, 101))
        now[0] = 11
        probe.turn()
        now[0] = 12
        probe.submit('sell', Intent('sell', 'AAPL', 'sell', 2, 200))
        probe.submit('short', Intent('short', 'AAPL', 'sell', 1, 200))
        now[0] = 13
        probe.turn()
        self.assertEqual(probe.sequencer.result('sell')['status'], 'accepted')
        self.assertEqual(probe.sequencer.result('short')['status'], 'rejected')
        self.assertEqual(dict(probe.execution.snapshot.reserved_shares), {'AAPL': 2})

    def test_latency_and_fee_are_not_bypassed_by_prepared_events(self):
        _, _, _, probe, now = self.probe(config=ExecutionConfig(latency_ns=15, fee_per_share=1))
        probe.submit('buy', Intent('buy', 'AAPL', 'buy', 2, 101))
        now[0] = 11
        probe.turn()
        self.assertEqual(probe.execution.snapshot.account.orders[0].filled, 0)
        self.assertEqual(probe.execution.snapshot.reserved_cash, 204)
        now[0] = 21
        probe.turn()
        self.assertEqual(probe.execution.snapshot.account.orders[0].filled, 2)
        self.assertEqual(probe.execution.snapshot.account.cash, 9796)
        self.assertEqual(probe.execution.snapshot.account.fees, 2)

    def test_two_80_value_orders_cannot_reserve_more_than_100(self):
        _, _, _, probe, now = self.probe(cash=100)
        probe.submit('a', Intent('a', 'AAPL', 'buy', 1, 80))
        probe.submit('b', Intent('b', 'AAPL', 'buy', 1, 80))
        now[0] = 5
        probe.turn()
        self.assertEqual(probe.sequencer.result('a')['status'], 'accepted')
        self.assertEqual(probe.sequencer.result('b')['status'], 'rejected')
        self.assertEqual(probe.execution.snapshot.available_cash, 20)

    def test_end_cancels_unexecuted_and_releases_existing_orders(self):
        _, _, _, probe, now = self.probe()
        probe.submit('a', Intent('a', 'AAPL', 'buy', 5, 101))
        now[0] = 5
        probe.turn()
        now[0] = 30
        probe.cancel('late', probe.sequencer.result('a')['order_id'])
        now[0] = 31
        probe.turn()
        self.assertEqual(probe.sequencer.result('late')['status'], 'cancelled')
        self.assertTrue(probe.execution.closed)
        # Old accepted order consumes pre-end events even when execution was late.
        self.assertEqual(probe.execution.snapshot.account.orders[0].filled, 5)


class PreparedFailureTests(unittest.TestCase):
    setUp = PreparedBookFixture.setUp
    ready = PreparedBookFixture.ready
    probe = PreparedFinanceTests.probe

    def test_fault_during_commit_does_not_publish_result_or_reexecute(self):
        _, _, _, probe, now = self.probe()
        probe.submit('a', Intent('a', 'AAPL', 'buy', 1, 101))
        now[0] = 5
        with patch.object(
            probe._commands, 'commit', side_effect=OSError('injected financial failure')
        ):
            with self.assertRaises(OSError):
                probe.turn()
        self.assertTrue(probe.execution.closed)
        self.assertEqual(probe.sequencer.snapshot()['state'], 'failed')
        self.assertEqual(probe.sequencer.result('a')['status'], 'failed')

    def test_end_arrives_during_first_commit_second_command_is_cancelled(self):
        _, _, _, probe, now = self.probe()
        probe.submit('a', Intent('a', 'AAPL', 'buy', 1, 80))
        probe.submit('b', Intent('b', 'AAPL', 'buy', 1, 80))
        now[0] = 5
        original = probe._apply

        def delayed(command):
            result = original(command)
            now[0] = 31
            return result

        probe.timeline.apply = delayed
        probe.turn()
        self.assertEqual(probe.sequencer.result('a')['status'], 'accepted')
        self.assertEqual(probe.sequencer.result('b')['status'], 'cancelled')
        self.assertTrue(probe.execution.closed)
