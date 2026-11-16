"""Each reference Agent in a realistic run: a real Agent process, dated price bars, news,
fundamentals and filings, turn-based pacing, orders, the journal, usage by role and
Replay. The rehearsal provider stands in for the model, so no model is called."""

import csv
import datetime
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from sim_platform import Platform
from sim_platform.market.news_import import convert_documents
from sim_platform.models.contracts import ModelProfile
from sim_platform.models.providers.rehearsal import RehearsalProvider
from sim_platform.reference_agents import CATALOGUE, latest_beliefs
from sim_platform.research.evaluation import agent_model_usage
from support.agents import settings
from support.clock import until

DAY_NS = 86_400 * 10**9
FIRST = datetime.date(2021, 3, 1)  # a Monday
SYMBOL = 'ACME'


def write_market(root):
    bars = root / 'acme.csv'
    with bars.open('w', newline='') as stream:
        out = csv.writer(stream)
        out.writerow(['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
        for i in range(10):
            price = 50 + i * 0.5
            day = FIRST + datetime.timedelta(days=i + (2 if i >= 5 else 0))
            out.writerow([day.isoformat(), price, price + 1, price - 1, price + 0.2, 500_000])
    news = root / 'news.json'
    news.write_text(
        json.dumps(
            [
                dict(
                    record_id='n1',
                    available_time_ns=DAY_NS + 3600 * 10**9,
                    period='2020Q4',
                    headline='ACME raises its outlook',
                ),
                dict(
                    record_id='n2',
                    available_time_ns=6 * DAY_NS,
                    period='2021Q1',
                    headline='ACME wins a large contract',
                ),
            ]
        )
    )
    facts = root / 'fundamentals.csv'
    facts.write_text(
        'symbol,metric,period_end,value,available_date\n'
        f'{SYMBOL},Revenue,2020-12-31,1200,2021-02-20\n'
        f'{SYMBOL},Revenue,2021-03-31,1300,2021-04-25\n'
    )
    filings = root / 'filings.csv'
    filings.write_text(
        'published_at,title,text,form\n'
        '2021-02-25,Annual report,Revenue guidance for 2021 is higher. Margins are stable.,10-K\n'
    )
    documents = root / 'documents.json'
    convert_documents(filings, documents, FIRST.isoformat(), form_column='form')
    return bars, news, facts, documents


@unittest.skipIf(
    os.environ.get('MARKET_LAB_SKIP_AGENT_PROCESSES') == '1', 'Agent processes disabled'
)
class ReferenceAgentScenarioTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bars, self.news, self.facts, self.documents = write_market(self.root)
        self.app = Platform(self.root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Research')
        self.app.set_model_credential('rehearsal', 'unused')

    def import_and_prepare(self, key, parameters=None):
        saved = self.app.import_reference_agent(
            self.profile,
            key,
            parameters=parameters,
            model_profile_ref='rehearsal',
            schedule=dict(
                mode='interval',
                interval_seconds='86400',
                busy_event_policy='preserve_interval',
                events=[],
            ),
        )
        python = f'{sys.version_info.major}.{sys.version_info.minor}'
        job = self.app.prepare_agent_environment(
            self.profile,
            saved['agent_id'],
            python_archive=(
                os.environ.get('AGENT_TEST_RUNTIME_ARCHIVE')
                if not self.app.python_runtimes()
                else None
            ),
        )
        try:
            state = job.wait(120)
            self.assertEqual(state['state'], 'ready', state)
        finally:
            job.close()
        self.assertTrue(python)
        return saved

    def run_agent(self, saved):
        account = self.app.create_account(self.profile, 'Cash ' + saved['agent_id'][:6], 10_000_000)
        research = self.app.open_research(method='agent')
        self.addCleanup(research.close)
        research.load(self.bars, SYMBOL, self.news)
        config = dict(
            settings(),
            symbol=SYMBOL,
            start_ns=str(DAY_NS + 10 * 3600 * 10**9),
            end_ns=str(4 * DAY_NS + 15 * 3600 * 10**9),
            speed='turn',
            granted_tools=saved['definition']['requires'],
            fundamentals_path=str(self.facts),
            documents_path=str(self.documents),
        )
        research.create_experiment(
            self.profile,
            account,
            source=self.bars,
            method='agent',
            agent_id=saved['agent_id'],
            revision=saved['revision'],
            settings=config,
            execution_mode='live',
        )
        self.app.configure_experiment_model(
            research,
            ModelProfile('openai-responses', 'rehearsal', 'rehearsal'),
            provider=RehearsalProvider(SYMBOL),
        )
        research.play()
        until(lambda: research.view()['execution']['status'] != 'running', 180)
        run_id = research.run_id
        research.close()
        return run_id, account

    def check_run(self, run_id, account, roles):
        events = self.app.run_events(self.profile, run_id)
        fills = [e for e in events if e['kind'] == 'fill']
        self.assertTrue(fills, [e['kind'] for e in events])
        self.assertTrue(all(f['data']['quantity'] == 1 for f in fills))
        archive = self.app.agent_log_archive(self.profile, run_id)
        tools = {
            (r.get('request') or {}).get('tool')
            for r in archive['records']
            if r.get('kind') == 'tool'
        }
        usage = agent_model_usage(archive)
        self.assertEqual(usage['served_models'], ['rehearsal'])
        self.assertLessEqual(set(roles), set(usage['by_label']))
        notes = self.app.agent_journal(self.profile, run_id)
        self.assertTrue(notes)
        return tools, notes

    def test_trading_firm_reads_every_source_and_trades(self):
        saved = self.import_and_prepare('trading_agents')
        run_id, account = self.run_agent(saved)
        tools, notes = self.check_run(
            run_id,
            account,
            [
                'technical analyst',
                'news analyst',
                'fundamentals analyst',
                'bull researcher',
                'bear researcher',
                'risk manager',
                'trader',
            ],
        )
        self.assertLessEqual(
            {'market.quote', 'news.list', 'fundamentals.get', 'orders.submit_market'}, tools
        )
        self.assertTrue(any(n['text'].startswith('Risk manager:') for n in notes))

    def test_memory_trader_runs_with_reflections(self):
        saved = self.import_and_prepare('layered_memory')
        run_id, account = self.run_agent(saved)
        _, notes = self.check_run(run_id, account, ['trader', 'reflection'])
        self.assertTrue(any(n['text'].startswith('Lesson') for n in notes))

    def test_manager_carries_its_beliefs_into_the_next_run(self):
        saved = self.import_and_prepare('manager_analyst')
        run_id, account = self.run_agent(saved)
        _, notes = self.check_run(run_id, account, ['manager', 'risk control'])
        beliefs = latest_beliefs(notes)
        self.assertEqual(beliefs, 'Rehearsal beliefs.')
        follow_up = self.app.import_reference_agent(
            self.profile,
            'manager_analyst',
            parameters=dict(CATALOGUE['manager_analyst']['parameters'], beliefs=beliefs),
        )
        self.assertEqual(follow_up['definition']['parameters']['beliefs'], beliefs)


if __name__ == '__main__':
    unittest.main()
