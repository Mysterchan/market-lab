"""IPC reply handoff must allow reuse without accepting unfinished duplicates."""

from concurrent.futures import Future
import errno
import multiprocessing
from threading import Event
import unittest
from unittest.mock import patch

from sim_platform.agents.hosting.bridge import ToolPipeBridge
from sim_platform.agents.hosting.protocol import TransportLimits, packet, unpack
from support.clock import until


class HeldSend:
    """Expose a delivered frame before the sending thread returns to bookkeeping."""

    def __init__(self, connection):
        self.connection = connection
        self.release = Event()

    def send_bytes(self, raw):
        self.connection.send_bytes(raw)
        if not self.release.wait(3):
            raise TimeoutError('test did not release reply sender')

    def close(self):
        self.connection.close()


class AgentIPCBridgeTests(unittest.TestCase):
    def prepare(self, submit, *, hold_send=False, start=True):
        context = multiprocessing.get_context('spawn')
        incoming, self.requests = context.Pipe(duplex=False)
        self.responses, outgoing = context.Pipe(duplex=False)
        self.addCleanup(self.requests.close)
        self.addCleanup(self.responses.close)
        self.sender = HeldSend(outgoing) if hold_send else outgoing
        self.bridge = ToolPipeBridge(
            submit, incoming, self.sender, TransportLimits(4096, 4096, 2, 1000)
        )
        self.addCleanup(self.bridge.close)
        if hold_send:
            self.addCleanup(self.sender.release.set)
        if start:
            self.bridge.start()

    def test_peer_close_during_poll_or_receive_is_not_a_transport_fault(self):
        for operation in ('poll', 'recv_bytes'):
            with self.subTest(operation=operation):
                self.prepare(lambda request: self.fail('closed peer submitted work'), start=False)
                with (
                    patch.object(self.bridge._incoming, 'poll', return_value=True),
                    patch.object(
                        self.bridge._incoming,
                        operation,
                        side_effect=BrokenPipeError(errno.EPIPE, 'peer closed pipe'),
                    ),
                ):
                    self.bridge._receive()
                self.assertIsNone(self.bridge.snapshot()['error'])
                self.assertTrue(self.bridge._stop.is_set())

    def test_unexpected_read_error_is_still_a_transport_fault(self):
        self.prepare(lambda request: self.fail('failed pipe submitted work'), start=False)
        with patch.object(
            self.bridge._incoming, 'poll', side_effect=OSError(errno.EIO, 'unexpected read error')
        ):
            self.bridge._receive()
        self.assertIn('unexpected read error', self.bridge.snapshot()['error'])

    def send(self):
        self.requests.send_bytes(packet(dict(request=dict(request_id='same')), 4096))

    def receive(self):
        self.assertTrue(self.responses.poll(2), self.bridge.snapshot())
        return unpack(self.responses.recv_bytes())

    def test_received_reply_allows_identity_reuse_before_sender_returns(self):
        calls = []

        def submit(request):
            calls.append(request)
            result = Future()
            result.set_result(dict(result={'accepted': True}))
            return result

        self.prepare(submit, hold_send=True)
        self.send()
        first = self.receive()
        self.send()
        until(lambda: len(calls) == 2 or self.bridge.snapshot()['error'])
        self.assertIsNone(self.bridge.snapshot()['error'])
        self.assertEqual(self.bridge.snapshot()['received'], 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.bridge.snapshot()['pending'], 2)
        self.sender.release.set()
        self.assertEqual(self.receive(), first)
        until(lambda: self.bridge.snapshot()['delivered'] == 2)
        self.assertEqual(self.bridge.snapshot()['pending'], 0)

    def test_duplicate_before_result_is_ready_still_disconnects(self):
        self.prepare(lambda request: Future())
        self.send()
        until(lambda: self.bridge.snapshot()['received'] == 1)
        self.send()
        until(lambda: self.bridge.snapshot()['error'] is not None)
        self.assertEqual(self.bridge.snapshot()['error'], 'duplicate in-flight IPC identity')
