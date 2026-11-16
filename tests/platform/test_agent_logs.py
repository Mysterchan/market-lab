"""Local agent JSON recording and end-only archiving to existing run records."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sqlalchemy import event

from sim_platform import Platform, ContractError
from sim_platform.persistence.agent_log_files import read_agent_log
from sim_platform.persistence.sql.repositories.runs import RunRepository
from support.agents import definition, settings


def position():
    return dict(sequence='0', time_ns='0', boundary='0', ordinal='0')


class AgentLogTests(unittest.TestCase):
    def test_position_roundtrip_keeps_distinct_prefixes_and_rejects_ambiguous_counters(self):
        from sim_platform.domain.operation_position import OperationPosition

        expected = OperationPosition(sequence=7, time_ns=120, boundary=9, ordinal=3)
        self.assertEqual(OperationPosition.from_wire(expected.to_wire()), expected)
        for counter in ('01', '-1', '1.0', True, 1):
            with self.subTest(counter=counter), self.assertRaises(ContractError):
                OperationPosition.from_wire(dict(expected.to_wire(), sequence=counter))
        with self.assertRaises(ContractError):
            OperationPosition(sequence=True, time_ns=120, boundary=9, ordinal=3)

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.app = Platform(self.directory / 'accounts')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Owner')
        self.account = self.app.create_account(self.profile, 'Cash', 1_000_000)
        self.maker = self.app.create_maker(self.profile, 'Fixture', 'human', '1')
        self.app.create_agent(self.profile, definition())
        self.experiment = self.app.prepare_agent_experiment(
            self.profile, self.account, 'demo', 1, settings()
        )
        self.run = self.app.create_run(self.profile, self.account, self.maker, ['AAPL'])
        self.path = self.directory / 'operations.jsonl'
        self.log = self.app.create_agent_log(
            self.profile, self.experiment['id'], self.run, self.path
        )

    def record(self):
        self.log.record_tool(
            {'tool': 'orders.submit_limit', 'arguments': {'quantity': 5}},
            {'status': 'ok', 'order_id': 'existing-order'},
            position(),
            receipt_target_ns='0',
        )
        self.log.record_control('pause', position(), {'remaining_timeout_seconds': 10})

    def finish(self, status='completed'):
        self.app.finish_run(self.profile, self.run, status)
        self.log.finish(status)

    def test_tool_control_and_finish_have_zero_sql_and_no_hash_or_execution(self):
        before = self.app.snapshot(self.profile, self.account)
        statements = []

        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(self.app._database.engine, 'before_cursor_execute', capture)
        try:
            with patch(
                'sim_platform.application.replay_manifest.capture_source',
                side_effect=AssertionError('agent logging must not hash files'),
            ):
                self.record()
                self.log.finish('completed')
        finally:
            event.remove(self.app._database.engine, 'before_cursor_execute', capture)
        self.assertEqual(statements, [])
        self.assertEqual(self.app.snapshot(self.profile, self.account), before)
        self.assertIsNone(self.app.agent_log_archive(self.profile, self.run))
        self.assertEqual(len(self.path.read_text().splitlines()), 4)

    def test_append_keeps_previous_bytes_and_does_not_copy_caller_objects(self):
        prefix = self.path.read_bytes()
        request = {'tool': 'market.snapshot'}
        self.log.record_tool(request, {'price': '100'}, position())
        first = self.path.read_bytes()
        self.assertTrue(first.startswith(prefix))
        request['tool'] = 'changed-after-record'
        self.log.record_control('wake', position())
        self.assertTrue(self.path.read_bytes().startswith(first))
        self.log.finish('completed')
        archive = read_agent_log(self.path)
        self.assertEqual(archive['records'][0]['request']['tool'], 'market.snapshot')
        self.assertEqual([item['index'] for item in archive['records']], [1, 2])

    def test_finished_log_archives_once_preserving_final_market_and_execution(self):
        self.record()
        self.finish()
        with self.app._unit_of_work(write=True) as session:
            repo = RunRepository(session)
            repo.save_archive(self.run, {'fixture': 'market'})
            repo.save_execution_archive(self.run, {'fixture': 'execution'})
            before = repo.archive(self.run)
        reply = self.app.archive_agent_log(self.profile, self.run, self.path)
        self.assertEqual(reply, {'run_id': self.run, 'record_count': 2})
        self.assertEqual(self.app.archive_agent_log(self.profile, self.run, self.path), reply)
        with self.app._unit_of_work() as session:
            saved = RunRepository(session).archive(self.run)
        for key in before:
            self.assertEqual(saved[key], before[key])
        self.assertEqual(saved['agent_operations'], read_agent_log(self.path))
        with Platform(self.directory / 'accounts') as reopened:
            self.assertEqual(
                reopened.agent_log_archive(self.profile, self.run), saved['agent_operations']
            )

    def test_running_run_and_unfinished_log_cannot_be_archived(self):
        self.record()
        with self.assertRaisesRegex(ContractError, 'unfinished'):
            self.app.archive_agent_log(self.profile, self.run, self.path)
        self.log.finish('completed')
        with self.assertRaisesRegex(ContractError, 'after the run ends'):
            self.app.archive_agent_log(self.profile, self.run, self.path)
        self.assertIsNone(self.app.agent_log_archive(self.profile, self.run))

    def test_archive_commit_failure_preserves_file_and_can_retry(self):
        self.record()
        self.finish()
        original = self.path.read_bytes()

        def fail(connection):
            raise OSError('archive commit failure')

        event.listen(self.app._database.engine, 'commit', fail)
        try:
            with self.assertRaises(OSError):
                self.app.archive_agent_log(self.profile, self.run, self.path)
        finally:
            event.remove(self.app._database.engine, 'commit', fail)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertIsNone(self.app.agent_log_archive(self.profile, self.run))
        self.app.archive_agent_log(self.profile, self.run, self.path)
        self.assertEqual(
            self.app.agent_log_archive(self.profile, self.run)['end']['record_count'], 2
        )

    def test_foreign_profile_run_and_status_are_rejected(self):
        self.record()
        self.finish()
        other = self.app.create_profile('Other')
        other_account = self.app.create_account(other, 'Cash', 100)
        other_maker = self.app.create_maker(other, 'Other', 'human', '1')
        other_run = self.app.create_run(other, other_account, other_maker, ['AAPL'])
        for profile, run in ((other, self.run), (other, other_run)):
            with self.assertRaises(ContractError):
                self.app.archive_agent_log(profile, run, self.path)
        lines = self.path.read_text().splitlines()
        lines[-1] = json.dumps({'end': {'status': 'failed', 'record_count': 2}})
        self.path.write_text('\n'.join(lines) + '\n')
        with self.assertRaises(ContractError):
            self.app.archive_agent_log(self.profile, self.run, self.path)
        self.assertIsNone(self.app.agent_log_archive(self.profile, self.run))

    def test_conflicting_archive_does_not_overwrite_saved_record(self):
        self.record()
        self.finish()
        self.app.archive_agent_log(self.profile, self.run, self.path)
        before = self.app.agent_log_archive(self.profile, self.run)
        lines = self.path.read_text().splitlines()
        record = json.loads(lines[1])
        record['record']['result']['order_id'] = 'another-order'
        lines[1] = json.dumps(record)
        self.path.write_text('\n'.join(lines) + '\n')
        with self.assertRaisesRegex(ContractError, 'different agent operation archive'):
            self.app.archive_agent_log(self.profile, self.run, self.path)
        self.assertEqual(self.app.agent_log_archive(self.profile, self.run), before)

    def test_truncation_gaps_unknown_versions_and_trailing_lines_fail(self):
        self.record()
        self.finish()
        original = self.path.read_text()
        lines = original.splitlines()
        header = json.loads(lines[0])
        header['header']['version'] = 'agent-operations/v99'
        gap = json.loads(lines[1])
        gap['record']['index'] = 5
        mutations = [
            original[:-1],
            '\n'.join(lines[:-1]) + '\n',
            json.dumps(header) + '\n' + '\n'.join(lines[1:]) + '\n',
            lines[0] + '\n' + json.dumps(gap) + '\n' + '\n'.join(lines[2:]) + '\n',
            original + '{}\n',
            original.replace('"status":"completed"', '"status":NaN'),
        ]
        for value in mutations:
            self.path.write_text(value)
            with self.assertRaises(ContractError):
                self.app.archive_agent_log(self.profile, self.run, self.path)
        self.assertIsNone(self.app.agent_log_archive(self.profile, self.run))

    def test_existing_file_is_not_overwritten_and_close_keeps_unfinished_file(self):
        original = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            self.app.create_agent_log(self.profile, self.experiment['id'], self.run, self.path)
        self.log.close()
        self.assertEqual(self.path.read_bytes(), original)
        with self.assertRaises(ContractError):
            self.log.record_control('stop', position())
        with self.assertRaises(ContractError):
            read_agent_log(self.path)

    def test_invalid_record_does_not_increment_or_pollute_file(self):
        before = self.path.read_bytes()
        with self.assertRaises(ContractError):
            self.log.record_tool({}, {'price': float('nan')}, position())
        for counter in ('', '00', '-1', '1.0', '\uFF11', '1' * 21, True, 1):
            invalid_position = dict(position(), time_ns=counter)
            with self.subTest(counter=counter), self.assertRaises(ContractError):
                self.log.record_control('wake', invalid_position)
            with self.subTest(receipt=counter), self.assertRaises(ContractError):
                self.log.record_tool({}, {}, position(), receipt_target_ns=counter)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.log.record_control('wake', position()), 1)
        self.log.finish('stopped')
        with self.assertRaises(ContractError):
            self.log.record_control('wake', position())
        with self.assertRaises(ContractError):
            self.log.finish('stopped')

    def test_schema_is_v7_with_no_new_agent_journal_tables(self):
        with closing(sqlite3.connect(self.directory / 'accounts' / 'platform.sqlite')) as db, db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 7)
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        self.assertTrue(
            {'agent_journals', 'agent_journal_events', 'agent_requests'}.isdisjoint(tables)
        )


if __name__ == '__main__':
    unittest.main()
