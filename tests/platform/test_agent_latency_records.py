"""Probe storage must preserve tail samples and cross-thread correlation."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from agent_probe_records import ProbeRecords, ThreadProbeRecords


class AgentLatencyRecordTests(unittest.TestCase):
    def test_integer_precision_and_missing_values(self):
        records = ProbeRecords(integers=('time_ns',), floats=('delay_ms',), flags=('notified',))
        records.append(dict(time_ns=2**60 + 7, delay_ms=-0.5, notified=False))
        records.append(dict(time_ns=2**60 + 8))
        self.assertEqual(
            list(records),
            [dict(time_ns=2**60 + 7, delay_ms=-0.5, notified=False), dict(time_ns=2**60 + 8)],
        )

    def test_concurrent_columns_and_late_request_identity(self):
        records = ProbeRecords(integers=('start_ns', 'end_ns'), texts=('request_id',))

        def write(worker):
            for ordinal in range(500):
                value = worker * 1000 + ordinal
                index = records.append(dict(start_ns=value, end_ns=value + 10))
                records.update(index, dict(request_id=str(value)))

        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(write, range(4)))
        values = list(records)
        self.assertEqual(len(values), 2000)
        self.assertEqual(len({v['start_ns'] for v in values}), 2000)
        for value in values:
            self.assertEqual(value['end_ns'], value['start_ns'] + 10)
            self.assertEqual(value['request_id'], str(value['start_ns']))

    def test_export_does_not_retain_a_second_object_graph(self):
        records = ProbeRecords(integers=('time_ns',), texts=('phase',))
        records.append(dict(time_ns=10, phase='market'))
        exported = list(records)
        exported[0]['time_ns'] = 999
        self.assertEqual(list(records)[0]['time_ns'], 10)
        self.assertEqual(records.storage_bytes(), 16)

    def test_thread_stores_preserve_receive_decode_tokens(self):
        records = ThreadProbeRecords(integers=('time_ns',), texts=('request_id',))

        def write(worker):
            for ordinal in range(100):
                value = worker * 1000 + ordinal
                token = records.append(dict(time_ns=value))
                records.update(token, dict(request_id=str(value)))

        # Dedicated threads rather than executor task reuse: prove distinct
        # producer stores without depending on executor scheduling.
        from threading import Thread

        threads = [Thread(target=write, args=(worker,)) for worker in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(records.stores), 4)
        values = list(records)
        self.assertEqual(len(values), 400)
        self.assertTrue(all(str(v['time_ns']) == v['request_id'] for v in values))


if __name__ == '__main__':
    unittest.main()
