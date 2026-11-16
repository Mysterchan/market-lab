"""Value-only IPC and real child resource boundaries."""

from dataclasses import replace
import json
import multiprocessing
import unittest
from unittest.mock import patch

from sim_platform import ContractError
from sim_platform.agents.tools.reads.capture import capture_read
from sim_platform.agents.tools.worker.host import ToolProcessHost
from sim_platform.agents.tools.protocol.transport import encode_job, decode_job, MAX_JOB_BYTES
from sim_platform.agents.tools.protocol.contracts import ToolError, validate_request
from support.tools import ReadFixture
from support.failures import expected_child_failure
from support.tools import request, position


class BrokenStartupFormatter:
    def __init__(self, binding):
        raise RuntimeError('intentional startup failure')


class ToolTransportTests(unittest.TestCase):
    def setUp(self):
        fixture = ReadFixture()
        fixture.prepare_reads()
        self.fixture = fixture
        self.binding = fixture.config

    def capture(self, tool='market.quote', arguments=None):
        arguments = {'symbol': 'AAPL'} if arguments is None else arguments
        req = validate_request(request(tool, arguments), self.binding)
        return capture_read(req, position(80_000_000_000), self.fixture.capture, '1')

    def test_capture_roundtrip_pins_rows_and_excludes_unneeded_orders(self):
        capture = self.capture()
        before = self.fixture.tools.execute(capture)
        identity, detached = decode_job(encode_job(7, capture))
        self.assertEqual(identity, 7)
        self.assertEqual(detached.execution.account.orders, ())
        self.fixture.market = replace(self.fixture.market, bids=((9_000_000, 99),))
        self.assertEqual(self.fixture.tools.execute(detached), before)
        detached.request['arguments']['symbol'] = 'changed'
        self.assertEqual(capture.request['arguments']['symbol'], 'AAPL')

    def test_compact_history_rows_preserve_filter_and_bar_semantics(self):
        from sim_platform.market.window import HistoryCapture

        capture = self.capture()
        history = HistoryCapture(
            80_000_000_000,
            ((1, 70_000_000_000, 9, 1_010_000, 3, 1, b'P', 86_400_000_000_000),),
            ((60_000_000_000, 1_010_000, 1_010_000, 1_010_000, 1_010_000, 3, 1, 3_030_000),),
            1,
            1,
        )
        captured = replace(capture, history=history)
        _, detached = decode_job(encode_job(1, captured))
        self.assertEqual(detached.history.materialize(), history.materialize())
        self.assertEqual(self.fixture.tools.execute(detached), self.fixture.tools.execute(captured))

    def test_order_capture_keeps_version_and_exact_account_sequence(self):
        capture = self.capture('orders.get', dict(order_id='o1'))
        identity, detached = decode_job(encode_job(3, capture))
        self.assertEqual(len(detached.execution.account.orders), 1)
        self.assertEqual(detached.order_versions['o1'], 1)
        self.assertEqual(self.fixture.tools.execute(detached)['cutoff']['account_sequence'], '7')

    def test_wire_refuses_unknown_type_version_and_large_frame(self):
        valid = json.loads(encode_job(1, self.capture()))
        for field, value in [
            ('unexpected', 'unknown'),
            ('capture', dict(type='ExecutionSession', value={})),
        ]:
            message = dict(valid, **{field: value})
            with self.assertRaises(ContractError):
                decode_job(json.dumps(message).encode())
        with self.assertRaises(ContractError):
            decode_job(b'x' * (MAX_JOB_BYTES + 1))
        with self.assertRaises(ContractError):
            encode_job(1, object())

    def test_snapshot_size_is_checked_by_sender_before_pipe_send(self):
        captured = replace(self.capture(), history=dict(extra='x' * MAX_JOB_BYTES))
        with self.assertRaises(ToolError) as error:
            encode_job(1, captured)
        self.assertEqual(error.exception.code, 'OUTPUT_TOO_LARGE')

    @expected_child_failure('RuntimeError: intentional startup failure')
    def test_spawn_startup_failure_reaps_child(self):
        before = {p.pid for p in multiprocessing.active_children()}
        with self.assertRaisesRegex(ContractError, 'ready'):
            ToolProcessHost(self.binding, formatter_factory=BrokenStartupFormatter)
        after = {p.pid for p in multiprocessing.active_children()}
        self.assertEqual(after, before)

    def test_failed_close_retains_runner_for_retry(self):
        # The normal runner lifetime remains the owner of the tool host.
        from support.async_reads import AsyncReadFixture
        from contextlib import ExitStack

        with ExitStack() as resources:
            fixture = AsyncReadFixture()
            fixture.prepare(resources.callback)
            experiment = fixture.open()
            worker = experiment.runner._tools._worker
            with patch.object(worker, 'close', side_effect=ContractError('tools still draining')):
                with self.assertRaisesRegex(ContractError, 'tools still draining'):
                    experiment.close()
            self.assertFalse(experiment.runner.closed)
            experiment.close()
            self.assertTrue(experiment.runner.closed)
