"""An Agent experiment over several symbols: one account, one merged timeline."""

import csv
import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sim_platform import ContractError, Platform
from sim_platform.agents.configuration import validate_run_configuration
from sim_platform.market.prepared.merged_timeline import MergedTimelineReader
from sim_platform.models.contracts import ModelProfile
from sim_platform.models.providers.rehearsal import RehearsalProvider
from support.agents import definition, settings, two_symbol_agent
from support.clock import until

DAY_NS = 86_400 * 10**9
FIRST = datetime.date(2021, 3, 1)


def write_bars(path, base):
    with path.open('w', newline='') as stream:
        out = csv.writer(stream)
        out.writerow(['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
        for i in range(6):
            price = base + i
            day = FIRST + datetime.timedelta(days=i)
            out.writerow([day.isoformat(), price, price + 1, price - 1, price + 0.5, 100_000])
    return path


class FakeReader:
    def __init__(self, times, directory):
        self.times = times
        self.manifest = dict(frames=len(times), first_ns=times[0], last_ns=times[-1])
        self.directory = directory

    def event_time(self, index):
        return self.times[index]

    def prefix(self, stamp):
        return sum(1 for t in self.times if t <= stamp)

    def market_sequence(self, prefix):
        return prefix

    def source_sequence(self, prefix):
        return prefix


class TimelineTests(unittest.TestCase):
    def test_merge_order_is_time_then_universe_order(self):
        reader = MergedTimelineReader(
            ['A', 'B'], [FakeReader([1, 3, 3], 'a'), FakeReader([2, 3], 'b')]
        )
        self.assertEqual([reader.event_time(i) for i in range(5)], [1, 2, 3, 3, 3])
        self.assertEqual([reader.symbol_at(i) for i in range(5)], ['A', 'B', 'A', 'A', 'B'])
        self.assertEqual(reader.prefix(2), 2)
        self.assertEqual(reader.market_sequence(4), 4)
        self.assertEqual(reader.manifest['universe'], ['A', 'B'])
        self.assertEqual(reader.directories, ['a', 'b'])


class ConfigurationTests(unittest.TestCase):
    def config(self, **changes):
        definition_ = dict(definition(), requires=['market.quote'])
        return dict(
            settings(),
            run_id='run-1',
            definition=definition_,
            initial_account_ref='a',
            execution_config_ref='e',
            **changes,
        )

    def test_symbols_must_start_with_the_symbol_and_be_distinct(self):
        validate_run_configuration(self.config(symbols=['AAPL', 'MSFT']))
        for bad in (['MSFT', 'AAPL'], ['AAPL', 'AAPL'], ['AAPL'], 'AAPL'):
            with self.assertRaises(ContractError):
                validate_run_configuration(self.config(symbols=bad))


class AgentTests(unittest.TestCase):
    def test_an_agent_reads_and_trades_two_symbols_from_one_account(self):
        temp = TemporaryDirectory()
        # Registered first, so it runs last: after the experiment and Platform close.
        self.addCleanup(temp.cleanup)
        if True:
            root = Path(temp.name)
            acme = write_bars(root / 'acme.csv', 50)
            beta = write_bars(root / 'beta.csv', 20)
            app = Platform(root / 'app')
            self.addCleanup(app.close)
            profile = app.create_profile('Multi')
            account = app.create_account(profile, 'Cash', 10_000_000)
            agent = definition()
            agent['requires'] = ['market.quote', 'orders.submit_market', 'control.finish']
            agent['schedule']['interval_seconds'] = '86400'
            app.create_agent(profile, agent)
            config = dict(
                settings(),
                symbol='ACME',
                symbols=['ACME', 'BETA'],
                start_ns=str(DAY_NS + 10 * 3600 * 10**9),
                end_ns=str(3 * DAY_NS + 15 * 3600 * 10**9),
                speed='turn',
                granted_tools=agent['requires'],
            )
            experiment = app.open_experiment(
                profile,
                account,
                source=[acme, beta],
                method='agent',
                agent_id='demo',
                revision=1,
                settings=config,
                execution_mode='live',
                agent_factory=two_symbol_agent,
            )
            self.addCleanup(experiment.close)
            app.set_model_credential('model-1', 'unused')
            app.configure_experiment_model(
                experiment,
                ModelProfile('openai-responses', 'rehearsal', 'model-1'),
                provider=RehearsalProvider('ACME'),
            )
            experiment.play()
            until(lambda: experiment.view()['execution']['status'] != 'running', 120)
            run_id = experiment.run_id
            log = experiment.runner._recorder.path
            experiment.close()  # seals and archives the Agent log
            run = app.run(profile, run_id, include_records=False)
            self.assertEqual(sorted(run['universe']), ['ACME', 'BETA'])
            positions = {p.symbol: p.quantity for p in app.snapshot(profile, account).positions}
            self.assertGreaterEqual(positions.get('BETA', 0), 1)
            self.assertNotIn('ACME', positions)
            archive = app.agent_log_archive(profile, run_id)
            quotes = [
                r
                for r in archive['records']
                if r.get('kind') == 'tool'
                and (r.get('request') or {}).get('tool') == 'market.quote'
            ]
            self.assertEqual(
                {r['request']['arguments']['symbol'] for r in quotes}, {'ACME', 'BETA'}
            )
            self.assertTrue(all(r['result']['status'] == 'ok' for r in quotes))
            prices = {
                r['request']['arguments']['symbol']: r['result']['data']['bid']['price_usd']
                for r in quotes
                if r['result']['data'].get('bid')
            }
            for r in quotes:
                self.assertEqual(r['result']['data']['symbol'], r['request']['arguments']['symbol'])
            # Each quote comes from its own symbol's book (ACME trades near 50, BETA near 20).
            self.assertEqual(set(prices), {'ACME', 'BETA'})
            self.assertGreater(float(prices['ACME']), float(prices['BETA']))
            # Replay rebuilds both symbols' markets and recalculates without the Agent.
            target = app.create_account(profile, 'Replay', 10_000_000)
            replayed = app.replay_prepared_operations(profile, target, log)
            self.assertTrue(replayed['matched'])
            self.assertTrue(replayed['events_verified'])
            self.assertEqual(replayed['model_calls'], 0)
            self.assertIn('fill', [e['kind'] for e in replayed['events']])


def two_symbol_news_agent(context):
    """Reads the news once, notes the headlines in the journal and finishes."""
    import json as json_module

    class Agent:
        def run(self, task):
            reply = context.tool('news.list').run(limit=10)
            headlines = [item['headline'] for item in reply['data']['items']]
            context.tool('journal.write').run(json_module.dumps(headlines))
            context.tool('control.finish').run()

    return Agent()


class NewsTests(unittest.TestCase):
    def test_an_agent_on_two_symbols_reads_point_in_time_news(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        acme = write_bars(root / 'acme.csv', 50)
        beta = write_bars(root / 'beta.csv', 20)
        news = root / 'news.json'
        import json as json_module

        news.write_text(
            json_module.dumps(
                [
                    dict(
                        record_id='a',
                        available_time_ns=DAY_NS,
                        period='Q1',
                        headline='ACME wins a contract',
                    ),
                    dict(
                        record_id='b',
                        available_time_ns=5 * DAY_NS,
                        period='Q1',
                        headline='BETA misses estimates',
                    ),
                ]
            )
        )
        app = Platform(root / 'app')
        self.addCleanup(app.close)
        profile = app.create_profile('Multi')
        account = app.create_account(profile, 'Cash', 10_000_000)
        agent = definition()
        agent['requires'] = ['news.list', 'journal.write', 'control.finish']
        agent['schedule']['interval_seconds'] = '86400'
        app.create_agent(profile, agent)
        research = app.open_research(method='agent', agent_factory=two_symbol_news_agent)
        self.addCleanup(research.close)
        research.load(acme, 'ACME', news)
        research.create_experiment(
            profile,
            account,
            source=[acme, beta],
            agent_id='demo',
            revision=1,
            settings=dict(
                settings(),
                symbol='ACME',
                symbols=['ACME', 'BETA'],
                start_ns=str(DAY_NS + 10 * 3600 * 10**9),
                end_ns=str(2 * DAY_NS + 15 * 3600 * 10**9),
                speed='turn',
                granted_tools=agent['requires'],
            ),
            execution_mode='live',
        )
        app.set_model_credential('model-1', 'unused')
        research.play()
        until(lambda: research.view()['execution']['status'] != 'running', 120)
        run_id = research.run_id
        research.close()
        notes = app.agent_journal(profile, run_id)
        # Only the news published before the Agent's first turn is visible.
        self.assertEqual(json_module.loads(notes[0]['text']), ['ACME wins a contract'])


class PolicyTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.sources = [write_bars(root / 'acme.csv', 50), write_bars(root / 'beta.csv', 20)]
        self.app = Platform(root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Multi')
        self.account = self.app.create_account(self.profile, 'Cash', 10_000_000)

    def test_a_rule_trades_every_symbol_on_its_own(self):
        experiment = self.app.open_experiment(
            self.profile,
            self.account,
            source=self.sources,
            method='rule',
            execution_mode='controlled',
            settings=dict(symbols=['ACME', 'BETA'], quantity=3, hold_ns=DAY_NS),
        )
        self.addCleanup(experiment.close)
        while experiment.runner.active:
            experiment.step()
        events = self.app.run_events(self.profile, experiment.run_id)
        intents = [e['data']['intent'] for e in events if e['kind'] == 'intent']
        self.assertEqual(
            {(i['client_id'], i['symbol']) for i in intents if i['side'] == 'buy'},
            {('rule-buy-ACME', 'ACME'), ('rule-buy-BETA', 'BETA')},
        )
        self.assertTrue({'rule-sell-ACME', 'rule-sell-BETA'} <= {i['client_id'] for i in intents})

    def test_live_manual_orders_reach_any_symbol(self):
        experiment = self.app.open_experiment(
            self.profile,
            self.account,
            source=self.sources,
            method='human',
            execution_mode='live',
            settings=dict(symbols=['ACME', 'BETA'], start_mode='first_book'),
        )
        self.addCleanup(experiment.close)
        experiment.play()
        experiment.order('o1', 'buy', 1, order_type='market', symbol='BETA')
        until(
            lambda: any(
                e['kind'] == 'accepted'
                for e in self.app.run_events(self.profile, experiment.run_id)
            ),
            30,
        )
        experiment.stop()
        events = self.app.run_events(self.profile, experiment.run_id)
        intent = next(e['data'] for e in events if e['kind'] == 'intent')
        self.assertEqual(intent['market_order']['symbol'], 'BETA')


if __name__ == '__main__':
    unittest.main()
