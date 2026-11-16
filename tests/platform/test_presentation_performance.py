"""Saved-run performance presentation: fill sides, equity marks and drawdown."""

import unittest

from sim_platform.desktop.presentation import fill_rows, performance

MINUTE = 60_000_000_000


def bar(index, open_, close):
    return dict(
        start_ns=index * MINUTE,
        end_ns=(index + 1) * MINUTE,
        open=open_,
        close=close,
        high=max(open_, close),
        low=min(open_, close),
        volume=10,
        complete=True,
    )


def fill(order, time_ns, quantity, price, fee=0):
    return dict(
        kind='fill',
        time_ns=time_ns,
        data=dict(order_id=order, quantity=quantity, price=price, fee=fee),
    )


def saved(fills, bars, *, positions=()):
    return dict(
        mode='saved',
        run=dict(initial_snapshot=dict(cash=1_000_000, positions=list(positions))),
        account=dict(
            orders=[dict(order_id='b', side='buy'), dict(order_id='s', side='sell')],
            positions=[],
        ),
        bars=bars,
        fills=fills,
        events=[],
    )


class PerformanceTests(unittest.TestCase):
    def test_equity_marks_position_at_each_close_and_reports_drawdown(self):
        bars = [bar(0, 100_000, 100_000), bar(1, 100_000, 120_000), bar(2, 120_000, 90_000)]
        view = saved(
            [fill('b', MINUTE // 2, 2, 100_000, fee=100), fill('s', 2 * MINUTE + 1, 1, 110_000)],
            bars,
        )
        result = performance(view)
        self.assertEqual(
            [value for _, value in result['series']],
            [1_000_000, 999_900, 1_039_900, 999_900],
        )
        self.assertEqual(result['max_drawdown'], 40_000)
        self.assertAlmostEqual(result['max_drawdown_ratio'], 40_000 / 1_039_900)
        self.assertEqual(result['traded_shares'], 3)
        self.assertEqual([row['side'] for row in result['fills']], ['buy', 'sell'])

    def test_curve_is_withheld_when_it_cannot_be_derived(self):
        bars = [bar(0, 100_000, 100_000)]
        held = saved([], bars, positions=[dict(symbol='AAPL', quantity=1, cost=1)])
        self.assertIsNone(performance(held)['series'])
        self.assertIsNone(performance(saved([], []))['series'])
        unknown = saved([fill('missing', 1, 1, 100_000)], bars)
        self.assertIsNone(performance(unknown)['series'])
        live = saved([], bars)
        del live['fills']
        live['events'] = [fill('b', 1, 1, 100_000)]
        self.assertIsNone(performance(live)['series'])
        self.assertEqual(fill_rows(live)[0]['side'], 'buy')


if __name__ == '__main__':
    unittest.main()
