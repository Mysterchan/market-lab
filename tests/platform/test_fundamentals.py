"""Point-in-time fundamentals: no figure is visible before it was published."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sim_platform import ContractError
from sim_platform.market.fundamentals import (
    NS_PER_DAY,
    FundamentalFact,
    FundamentalStore,
    load_edgar_company_facts,
    load_fundamentals_csv,
    session_time_ns,
)


def fact(period, value, day, metric='Revenue'):
    return FundamentalFact('ACME', metric, period, value, day * NS_PER_DAY)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = FundamentalStore(
            [
                fact('2020-03-31', 100.0, 30),
                fact('2020-06-30', 110.0, 120),
                # The Q1 figure is restated on day 150.
                fact('2020-03-31', 95.0, 150),
                fact('2020-06-30', 2.0, 120, metric='EPS'),
            ]
        )

    def test_nothing_is_visible_before_publication(self):
        self.assertEqual(self.store.as_of(29 * NS_PER_DAY, 'ACME'), {})
        self.assertEqual(self.store.metrics('ACME', 29 * NS_PER_DAY), [])

    def test_latest_periods_come_first(self):
        view = self.store.as_of(130 * NS_PER_DAY, 'ACME', ['Revenue'])
        self.assertEqual(
            [(r['period_end'], r['value']) for r in view['Revenue']],
            [('2020-06-30', 110.0), ('2020-03-31', 100.0)],
        )

    def test_restatement_stays_hidden_until_its_own_publication(self):
        before = self.store.as_of(149 * NS_PER_DAY, 'ACME', ['Revenue'], periods=4)
        after = self.store.as_of(150 * NS_PER_DAY, 'ACME', ['Revenue'], periods=4)
        self.assertEqual(before['Revenue'][1]['value'], 100.0)
        self.assertEqual(after['Revenue'][1]['value'], 95.0)

    def test_bounds(self):
        with self.assertRaises(ContractError):
            self.store.as_of(0, 'ACME', periods=0)
        with self.assertRaises(ContractError):
            self.store.as_of(0, 'ACME', [f'm{i}' for i in range(51)])
        with self.assertRaises(ContractError):
            FundamentalFact('ACME', 'Revenue', 'not-a-date', 1.0, 0)
        with self.assertRaises(ContractError):
            FundamentalFact('ACME', 'Revenue', '2020-01-01', float('nan'), 0)

    def test_session_time_is_conservative(self):
        self.assertEqual(session_time_ns('2020-01-10', '2020-01-06'), 5 * NS_PER_DAY)
        self.assertEqual(
            session_time_ns('2020-01-10', '2020-01-06', next_day=False), 4 * NS_PER_DAY
        )
        self.assertEqual(session_time_ns('2019-12-01', '2020-01-06'), 0)


class LoaderTests(unittest.TestCase):
    def test_csv_uses_the_publication_date(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'f.csv'
            path.write_text(
                'symbol,metric,period_end,value,available_date,unit\n'
                'ACME,Revenue,2020-03-31,100,2020-04-30,USD\n'
            )
            store = load_fundamentals_csv(path, '2020-01-02')
            self.assertEqual(store.as_of(118 * NS_PER_DAY, 'ACME'), {})
            (row,) = store.as_of(120 * NS_PER_DAY, 'ACME')['Revenue']
            self.assertEqual((row['value'], row['unit']), (100.0, 'USD'))
            path.write_text('symbol,metric,value\nACME,Revenue,1\n')
            with self.assertRaisesRegex(ContractError, 'columns'):
                load_fundamentals_csv(path, '2020-01-02')
            path.write_text(
                'symbol,metric,period_end,value,available_date\nACME,Revenue,2020-03-31,x,2020-04-30\n'
            )
            with self.assertRaisesRegex(ContractError, 'row 2'):
                load_fundamentals_csv(path, '2020-01-02')

    def test_edgar_company_facts_keep_every_filing(self):
        document = dict(
            cik=1,
            entityName='Acme',
            facts={
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                dict(end='2020-03-31', val=100, filed='2020-05-01', form='10-Q'),
                                dict(end='2020-03-31', val=97, filed='2021-02-20', form='10-K'),
                                dict(end='2020-06-30', val=120),
                            ]
                        }
                    }
                }
            },
        )
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'companyfacts.json'
            path.write_text(json.dumps(document))
            store = load_edgar_company_facts(path, 'ACME', '2020-01-02')
            self.assertEqual(len(store), 2)
            early = store.as_of(200 * NS_PER_DAY, 'ACME')['us-gaap:Revenues']
            late = store.as_of(500 * NS_PER_DAY, 'ACME')['us-gaap:Revenues']
            self.assertEqual((early[0]['value'], early[0]['form']), (100.0, '10-Q'))
            self.assertEqual((late[0]['value'], late[0]['form']), (97.0, '10-K'))
            path.write_text('[]')
            with self.assertRaises(ContractError):
                load_edgar_company_facts(path, 'ACME', '2020-01-02')


if __name__ == '__main__':
    unittest.main()
