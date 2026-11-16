"""Point-in-time data sources: text disguise, news/document import, macro vintages,
connectors, universe membership, document tools and table export."""

import csv
import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from sim_platform import ContractError, Platform
from sim_platform.agents.tools.protocol.contracts import ToolError, validate_tool_arguments
from sim_platform.agents.tools.reads.formatter import ReadFormatter
from sim_platform.market.documents import DocumentStore, load_documents
from sim_platform.market.fundamentals import NS_PER_DAY, load_alfred_csv, load_fundamentals_path
from sim_platform.market.news_import import convert_documents, convert_news
from sim_platform.market.prepared.disguise_text import shift_text
from sim_platform.research.universe import load_membership, members_on, survivorship_check

DAY = datetime.date(2020, 1, 6)


def write_bars(path, days=12):
    with path.open('w', newline='') as stream:
        out = csv.writer(stream)
        out.writerow(['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
        for i in range(days):
            price = 100 + i
            out.writerow(
                [
                    (DAY + datetime.timedelta(days=i)).isoformat(),
                    price,
                    price + 1,
                    price - 1,
                    price,
                    1000,
                ]
            )
    return path


class DisguiseTextTests(unittest.TestCase):
    def test_names_dates_and_years_move_together(self):
        text = shift_text(
            'Apple Inc beat in 2020Q1; AAPL rose on 2020-04-30',
            28 * 365,
            {'Apple Inc': 'the company', 'Apple': 'the company', 'AAPL': 'ASSET1'},
        )
        self.assertNotIn('Apple', text)
        self.assertIn('ASSET1', text)
        self.assertIn('2048Q1', text)
        self.assertIn(
            (datetime.date(2020, 4, 30) + datetime.timedelta(days=28 * 365)).isoformat(), text
        )

    def test_platform_disguises_news_and_fundamentals_with_the_prices(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            bars = write_bars(root / 'aapl.csv')
            news = root / 'news.json'
            news.write_text(
                json.dumps(
                    [
                        dict(
                            record_id='1',
                            available_time_ns=5,
                            period='2020Q1',
                            headline='Apple raises guidance',
                        )
                    ]
                )
            )
            facts = root / 'f.csv'
            facts.write_text(
                'symbol,metric,period_end,value,available_date\n'
                'AAPL,Revenue,2019-12-31,1,2020-01-28\n'
            )
            with Platform(root / 'app') as app:
                mapping = app.disguise_recording(
                    bars, news=news, fundamentals=facts, names=['Apple', 'AAPL']
                )
            shift = datetime.timedelta(days=mapping['shift_days'])
            (record,) = json.loads(Path(mapping['news_output']).read_text())
            self.assertEqual(record['headline'], 'the company raises guidance')
            self.assertEqual(record['available_time_ns'], 5)
            with Path(mapping['fundamentals_output']).open() as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row['symbol'], 'ASSET1')
            self.assertEqual(
                row['available_date'], (datetime.date(2020, 1, 28) + shift).isoformat()
            )


class ImportTests(unittest.TestCase):
    def test_news_and_documents_land_at_their_publication_time(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'feed.jsonl'
            source.write_text(
                json.dumps(dict(published_at='2020-01-07T09:30:00Z', headline='Early', id='a'))
                + '\n'
                + json.dumps(dict(published_at='2019-12-31T12:00:00', headline='Before'))
                + '\n'
            )
            result = convert_news(
                source, root / 'news.json', '2020-01-06', id_column=None, delay_seconds=60
            )
            records = json.loads((root / 'news.json').read_text())
            self.assertEqual(result['records'], 2)
            self.assertEqual(records[0]['available_time_ns'], 0)
            self.assertEqual(
                records[1]['available_time_ns'], NS_PER_DAY + (9 * 3600 + 30 * 60 + 60) * 10**9
            )
            docs = root / 'filings.csv'
            docs.write_text(
                'published_at,title,text,form\n2020-01-08,10-K,Revenue grew. Margin fell.,10-K\n'
            )
            convert_documents(docs, root / 'docs.json', '2020-01-06', form_column='form')
            store = load_documents(root / 'docs.json')
            self.assertEqual(store.search(NS_PER_DAY, 'margin'), [])
            (hit,) = store.search(3 * NS_PER_DAY, 'margin')
            self.assertEqual((hit['form'], hit['matches']), ('10-K', 1))
            page = store.read(3 * NS_PER_DAY, hit['document_id'])
            self.assertIn('Margin fell', page['text'])
            with self.assertRaises(ContractError):
                store.read(NS_PER_DAY, hit['document_id'])

    def test_alfred_vintages_and_folders(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'cpi.csv').write_text(
                'observation_date,CPI_20200110,CPI_20200210\n2019-12-01,100.0,100.4\n2020-01-01,.,101.0\n'
            )
            (root / 'f.csv').write_text(
                'symbol,metric,period_end,value,available_date\n' 'X,EPS,2019-12-31,2,2020-01-20\n'
            )
            store = load_alfred_csv(root / 'cpi.csv', 'X', '2020-01-06')
            early = store.as_of(10 * NS_PER_DAY, 'X', ['macro:CPI'])['macro:CPI']
            late = store.as_of(40 * NS_PER_DAY, 'X', ['macro:CPI'])['macro:CPI']
            self.assertEqual([r['value'] for r in early], [100.0])
            self.assertEqual([r['value'] for r in late], [101.0, 100.4])
            both = load_fundamentals_path(root, 'X', '2020-01-06')
            self.assertEqual(both.metrics('X'), ['EPS', 'macro:CPI'])
            (root / 'bad.csv').write_text('a,b\n1,2\n')
            with self.assertRaisesRegex(ContractError, 'ALFRED'):
                load_fundamentals_path(root, 'X', '2020-01-06')


class ConnectorTests(unittest.TestCase):
    def test_connector_output_uses_the_standard_loaders(self):
        from sim_platform.market.connectors import export_connector
        from sim_platform.market.fundamentals import load_fundamentals_csv

        class Vendor:
            def fundamentals(self, symbol):
                yield dict(
                    symbol=symbol,
                    metric='Revenue',
                    period_end='2019-12-31',
                    value=5,
                    available_date='2020-02-01',
                )

            def news(self, symbol):
                yield dict(published_at='2020-01-07T10:00:00', headline=f'{symbol} news', id='n1')

            def documents(self, symbol):
                return []

        with TemporaryDirectory() as temp:
            written = export_connector(Vendor(), 'ACME', temp, '2020-01-06')
            self.assertIsNone(written['documents'])
            store = load_fundamentals_csv(written['fundamentals'], '2020-01-06')
            self.assertEqual(store.as_of(25 * NS_PER_DAY, 'ACME'), {})
            news = json.loads(Path(written['news']).read_text())
            self.assertEqual((news[0]['record_id'], news[0]['provenance']), ('n1', 'Vendor'))


class UniverseTests(unittest.TestCase):
    def test_survivorship_is_detected(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'members.csv'
            path.write_text(
                'symbol,start_date,end_date,reason\n'
                'AAA,2010-01-01,,\nBBB,2010-01-01,2015-06-01,delisted\n'
                'CCC,2018-01-01,,\n'
            )
            spells = load_membership(path)
            self.assertEqual(members_on(spells, '2014-01-02'), ['AAA', 'BBB'])
            check = survivorship_check(spells, ['AAA', 'CCC'], '2014-01-02', '2016-12-30')
            self.assertEqual(check['not_members'], ['CCC'])
            self.assertEqual(check['missing_leavers'], ['BBB'])
            self.assertEqual(check['left_during'], {'BBB': 'delisted'})
            self.assertTrue(check['biased'])
            fair = survivorship_check(spells, ['AAA', 'BBB'], '2014-01-02', '2016-12-30')
            self.assertFalse(fair['biased'])
            path.write_text('symbol,start_date,end_date\nX,2020-01-01,2019-01-01\n')
            with self.assertRaises(ContractError):
                load_membership(path)


class DocumentToolTests(unittest.TestCase):
    def test_arguments_and_formatting(self):
        validate_tool_arguments('documents.search', dict(query='margin', limit=5))
        validate_tool_arguments('documents.read', dict(document_id='d1', offset=0))
        for tool, arguments in (
            ('documents.search', dict(query='x' * 501, limit=5)),
            ('documents.search', dict(query='x', limit=21)),
            ('documents.read', dict(document_id='', offset=0)),
        ):
            with self.assertRaises(ToolError):
                validate_tool_arguments(tool, arguments)
        formatter = ReadFormatter(dict(limits=dict(event_buckets=4)))
        captured = SimpleNamespace(market=SimpleNamespace(symbol='ACME'))
        request = dict(
            tool='documents.search', arguments=dict(query='m', limit=1), work_id='w', epoch='1'
        )
        with self.assertRaises(ToolError):
            formatter._read(request, captured, dict(documents=None), {})
        with self.assertRaises(ToolError):
            formatter._read(request, captured, dict(documents=dict(error='not yet')), {})
        store = DocumentStore([dict(document_id='d1', available_time_ns=0, title='T', text='m')])
        data, _ = formatter._read(
            request, captured, dict(documents=dict(items=store.search(1, 'm'))), {}
        )
        self.assertEqual(data['items'][0]['available_time_ns'], '0')


class TableTests(unittest.TestCase):
    def test_reports_become_flat_tables_and_csv_files(self):
        from sim_platform.research.frames import export_report_tables, report_tables

        with TemporaryDirectory() as temp:
            root = Path(temp)
            bars = write_bars(root / 'bars.csv')
            with Platform(root / 'app') as app:
                profile = app.create_profile('P')
                with app.open_batch() as batch:
                    report = batch.evaluate(
                        str(bars),
                        policy='rule',
                        hold_ns=NS_PER_DAY,
                        symbol='ASSET1',
                        profile=profile,
                    )
            tables = report_tables(report)
            self.assertTrue(tables['fills'])
            self.assertTrue(tables['equity'])
            self.assertEqual(len(tables['figures']), 1)
            written = export_report_tables(report, root / 'out')
            with open(written['fills']) as stream:
                self.assertIn('price', next(csv.reader(stream)))
            with self.assertRaises(ContractError):
                export_report_tables(report, root / 'out', file_format='xlsx')


class ParquetTests(unittest.TestCase):
    def test_parquet_bars_convert_to_the_csv_format(self):
        try:
            import pyarrow
            import pyarrow.parquet as parquet
        except ImportError:
            self.skipTest('pyarrow is not installed (pip install "market-lab[parquet]")')
        from sim_platform.market.parquet import convert_parquet_bars

        with TemporaryDirectory() as temp:
            root = Path(temp)
            table = pyarrow.table(
                dict(Date=[DAY], Open=[1.0], High=[2.0], Low=[0.5], Close=[1.5], Volume=[9])
            )
            parquet.write_table(table, root / 'bars.parquet')
            result = convert_parquet_bars(root / 'bars.parquet', root / 'bars.csv')
            self.assertEqual(result['rows'], 1)
            self.assertIn('2020-01-06', (root / 'bars.csv').read_text())

    def test_missing_pyarrow_is_explained(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            from sim_platform.market.parquet import convert_parquet_bars

            with self.assertRaisesRegex(ContractError, 'pyarrow'):
                convert_parquet_bars('x.parquet', 'x.csv')


if __name__ == '__main__':
    unittest.main()
