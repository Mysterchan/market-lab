"""Offline acceptance: a whole study runs with no connection beyond loopback.

An audit hook records every socket connection this process makes. The study prepares
markets, runs a rule and an Agent (served by a local model server), replays the Agent
run, evaluates both, verifies lineage and writes a model risk report. The Agent's own
process connects only to the loopback model server, which the server's request log
confirms. Offline mode also refuses the hosted provider and every download.
"""

import ipaddress
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from sim_platform import ContractError, Platform
from sim_platform.examples.configured_agent_demo import example_configuration
from sim_platform.examples.market_demo import write_fixture
from sim_platform.models.contracts import ModelProfile
from sim_platform.network import set_offline
from test_local_models import ChatServer

GRANTS = ['market.quote', 'control.finish', 'model.request', 'orders.submit_market']
CONNECTIONS = []


def _audit(event, arguments):
    if event == 'socket.connect':
        CONNECTIONS.append(arguments[1])


sys.addaudithook(_audit)


def _external(address):
    """True for an internet address (not loopback, private, link-local or a file path)."""
    if not isinstance(address, tuple):
        return False  # Unix domain sockets and pipes stay on this machine.
    try:
        ip = ipaddress.ip_address(address[0])
    except ValueError:
        return address[0] not in ('localhost',)
    return not (ip.is_loopback or ip.is_private or ip.is_link_local)


class OfflineTests(unittest.TestCase):
    def setUp(self):
        set_offline(True)
        self.addCleanup(set_offline, None)
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source, _ = write_fixture(self.root)
        self.app = Platform(self.root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Offline')

    def test_network_actions_are_refused(self):
        with self.assertRaises(ContractError):
            self.app.prepare_python_runtime('3.12', allow_download=True)
        with self.assertRaises(ContractError):
            self.app.prepare_model_environment(str(self.root), allow_download=True)

    def test_a_whole_study_stays_on_this_machine(self):
        CONNECTIONS.clear()
        server = ChatServer('AAPL')
        self.addCleanup(server.close)
        app, profile = self.app, self.profile
        account = app.create_account(profile, 'Rule', 100_000_000)
        rule = app.open_experiment(
            profile,
            account,
            source=self.source,
            method='rule',
            execution_mode='controlled',
            settings=dict(quantity=1, hold_ns=10**9),
        )
        while rule.runner.active:
            rule.step()
        rule_run = rule.run_id
        rule.close()

        definition, settings = example_configuration()
        definition['requires'] = settings['granted_tools'] = GRANTS
        app.create_agent(profile, definition)
        account = app.create_account(profile, 'Agent', 100_000_000)
        agent = app.open_experiment(
            profile,
            account,
            source=self.source,
            method='agent',
            agent_id=definition['agent_id'],
            revision=1,
            settings=dict(settings, speed='turn'),
            execution_mode='live',
        )
        self.addCleanup(agent.close)
        with self.assertRaises(ContractError):  # the hosted provider is refused offline
            app.configure_experiment_model(agent, ModelProfile('openai-responses', 'm', 'k'))
        app.configure_experiment_model(
            agent, ModelProfile('openai-compatible', 'local', base_url=server.url)
        )
        agent.play()
        started = time.monotonic()
        while agent.runner.active or not agent.runner.archive_ready:
            agent.tick(render=False)
            time.sleep(0.01)
            self.assertLess(time.monotonic() - started, 60)
        agent_run = agent.run_id
        log = agent.runner._recorder.path
        agent.close()

        replayed = app.replay_prepared_operations(
            profile, app.create_account(profile, 'Replay', 100_000_000), log
        )
        self.assertTrue(replayed['matched'])
        self.assertEqual(replayed['model_calls'], 0)
        evaluation = app.evaluation(profile, dict(rule=[rule_run], agent=[agent_run]))
        self.assertEqual(len(evaluation['entries']), 2)
        self.assertTrue(app.verify_run(profile, agent_run)['unchanged'])
        app.model_risk_report(profile, [rule_run, agent_run], path=self.root / 'review.md')
        self.assertTrue(server.requests)  # the Agent reached only the loopback server
        self.assertEqual([a for a in CONNECTIONS if _external(a)], [])


if __name__ == '__main__':
    unittest.main()
