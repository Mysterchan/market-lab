"""news.list and fundamentals.get: registration, argument checks, capture and format."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from sim_platform import ContractError
from sim_platform.agents.configuration import AVAILABLE_TOOLS
from sim_platform.agents.tools.client.collection import TOOL_TYPES
from sim_platform.agents.tools.protocol.contracts import (
    ARGUMENTS,
    OPTIONAL_ARGUMENTS,
    ToolError,
    validate_tool_arguments,
)
from sim_platform.agents.tools.reads.formatter import ReadFormatter
from sim_platform.application.experiments.agent import load_run_fundamentals
from sim_platform.application.experiments.agent_driver import PreparedToolCapture
from sim_platform.market import NewsRecord
from sim_platform.market.fundamentals import NS_PER_DAY, FundamentalFact, FundamentalStore

NEWS = (
    NewsRecord('n1', 1 * NS_PER_DAY, '2020Q1', 'Early headline'),
    NewsRecord('n2', 5 * NS_PER_DAY, '2020Q1', 'Later headline'),
)


class FakeReader:
    manifest = dict(calendar=dict(start_date='2020-01-02'))

    def snapshot_at(self, cutoff):
        return SimpleNamespace(symbol='ACME', time_ns=cutoff)

    def prefix(self, cutoff):
        return cutoff

    @property
    def history(self):
        return SimpleNamespace(capture=lambda prefix, cutoff, include_bars: dict(trades=()))

    def news_at(self, cutoff, limit=None):
        visible = tuple(n for n in NEWS if n.available_time_ns <= cutoff)
        return visible[-limit:] if limit else visible


def metadata():
    from dataclasses import dataclass

    from sim_platform.domain.models import AccountSnapshot

    @dataclass(frozen=True)
    class Execution:
        account: AccountSnapshot
        reserved_shares: tuple

    return Execution(AccountSnapshot('a', 'p', 'USD', 0, (), 0, 0, 0, ()), ())


class RegistrationTests(unittest.TestCase):
    def test_tools_are_registered_and_grantable(self):
        for name in ('news.list', 'fundamentals.get'):
            self.assertIn(name, TOOL_TYPES)
            self.assertIn(name, AVAILABLE_TOOLS)
            self.assertEqual(TOOL_TYPES[name].execution_route, 'market')
        self.assertEqual(set(ARGUMENTS['fundamentals.get']), {'symbol', 'periods'})
        self.assertEqual(set(OPTIONAL_ARGUMENTS['fundamentals.get']), {'metrics'})

    def test_fundamental_arguments_are_bounded(self):
        validate_tool_arguments('fundamentals.get', dict(symbol='ACME', periods=4))
        for bad in (
            dict(periods=0),
            dict(periods=21),
            dict(periods=4, metrics=[]),
            dict(periods=4, metrics=['x'] * 51),
            dict(periods=4, metrics=[3]),
        ):
            with self.assertRaises(ToolError):
                validate_tool_arguments('fundamentals.get', dict(symbol='ACME', **bad))


class CaptureTests(unittest.TestCase):
    def capture(self, store=None):
        return PreparedToolCapture(FakeReader(), metadata(), store)

    def test_news_stops_at_the_cutoff(self):
        values = self.capture()('news.list', 3 * NS_PER_DAY, dict(limit=10))
        self.assertEqual([n['record_id'] for n in values['history']['news']], ['n1'])

    def test_fundamentals_respect_publication_time(self):
        store = FundamentalStore(
            [FundamentalFact('ACME', 'Revenue', '2020-03-31', 100.0, 30 * NS_PER_DAY)]
        )
        capture = self.capture(store)
        early = capture('fundamentals.get', 29 * NS_PER_DAY, dict(periods=4))
        late = capture('fundamentals.get', 30 * NS_PER_DAY, dict(periods=4))
        self.assertEqual(early['history']['fundamentals'], {})
        self.assertEqual(late['history']['fundamentals']['Revenue'][0]['value'], 100.0)
        missing = self.capture()('fundamentals.get', 30 * NS_PER_DAY, dict(periods=4))
        self.assertIsNone(missing['history']['fundamentals'])


class FormatterTests(unittest.TestCase):
    def setUp(self):
        self.formatter = ReadFormatter(dict(limits=dict(event_buckets=4)))
        self.captured = SimpleNamespace(market=SimpleNamespace(symbol='ACME'))

    def read(self, tool, arguments, history):
        request = dict(tool=tool, arguments=arguments, work_id='w', epoch='1')
        return self.formatter._read(request, self.captured, history, {})[0]

    def test_news_is_newest_first_and_limited(self):
        from dataclasses import asdict

        data = self.read('news.list', dict(limit=1), dict(news=[asdict(n) for n in NEWS]))
        self.assertEqual([i['record_id'] for i in data['items']], ['n2'])
        self.assertEqual(data['items'][0]['available_time_ns'], str(5 * NS_PER_DAY))

    def test_fundamentals_without_a_file_report_unavailable(self):
        with self.assertRaises(ToolError) as caught:
            self.read('fundamentals.get', dict(symbol='ACME', periods=1), dict(fundamentals=None))
        self.assertEqual(caught.exception.code, 'DATA_UNAVAILABLE')
        rows = dict(Revenue=[dict(period_end='2020-03-31', value=1.0, available_time_ns=5)])
        data = self.read(
            'fundamentals.get', dict(symbol='ACME', periods=1), dict(fundamentals=rows)
        )
        self.assertEqual(data['metrics']['Revenue'][0]['available_time_ns'], '5')


class LoadTests(unittest.TestCase):
    def test_run_fundamentals_need_a_dated_recording(self):
        self.assertIsNone(load_run_fundamentals({}, FakeReader(), 'ACME'))
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'f.csv'
            path.write_text(
                'symbol,metric,period_end,value,available_date\n'
                'ACME,Revenue,2020-03-31,100,2020-04-30\n'
            )
            store = load_run_fundamentals(dict(fundamentals_path=str(path)), FakeReader(), 'ACME')
            self.assertEqual(store.symbols(), ['ACME'])
            undated = SimpleNamespace(manifest={})
            with self.assertRaisesRegex(ContractError, 'dated recording'):
                load_run_fundamentals(dict(fundamentals_path=str(path)), undated, 'ACME')


if __name__ == '__main__':
    unittest.main()
