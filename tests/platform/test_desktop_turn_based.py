"""New experiment offers Live or turn-based pacing for a saved Agent."""

import os
import time
import unittest

from support.desktop import DesktopFixture


@unittest.skipUnless(os.environ.get('TRADING_DESKTOP_TESTS') == '1', 'optional Desktop suite')
class DesktopTurnBasedTests(DesktopFixture, unittest.TestCase):
    def test_a_turn_based_agent_is_prepared_labelled_and_runs_to_its_end(self):
        from unittest.mock import patch
        from sim_platform import Platform
        from sim_platform.examples.configured_agent_demo import QuoteOnlyProvider

        self.click('[data-action="agents"]')
        self.click('[data-action="agent-new"]')
        self.js(
            "document.querySelector('[name=name]').value='Turn reader';"
            "document.querySelector('[name=model]').value='offline-test-model';"
            "document.querySelector('[name=prompt]').value='Read a quote, then finish';"
            "document.querySelector('#agent-form').requestSubmit()"
        )
        self.until(lambda: self.window.agent_catalogue.get('agents'))
        saved = self.window.agent_catalogue['agents'][0]
        self.window.dispatch(
            'save_credential',
            dict(reference='desktop-model', secret='local-test-key', persistence='session'),
        )
        original = Platform.configure_experiment_model
        with patch.object(
            Platform,
            'configure_experiment_model',
            lambda app, controller, profile: original(
                app, controller, profile, provider=QuoteOnlyProvider()
            ),
        ):
            self.until(lambda: self.js('document.querySelectorAll(".agent-card").length') == 1)
            self.click('[data-action="agent-use:' + saved['agent_id'] + '"]')
            self.js("document.querySelector('#setup-form').requestSubmit()")
            # Account & execution: both pacings are offered; Live is the default.
            self.until(
                lambda: self.js('document.querySelectorAll("[name=agent_pacing]").length') == 2
            )
            self.assertTrue(
                self.js('document.querySelector("[name=agent_pacing][value=live]").checked')
            )
            self.js(
                "document.querySelector('[name=agent_pacing][value=turn]').click();"
                "document.querySelector('[name=end_time]').value='09:30:06';"
                "document.querySelector('#setup-form').requestSubmit()"
            )
            self.until(lambda: 'Turn-based' in (self.js('document.body.textContent') or ''))
            sent = []
            self.window.browser.commands.requested.connect(
                lambda name, args: sent.append(args) if name == 'prepare' else None
            )
            self.js("document.querySelector('#setup-form').requestSubmit()")
            self.until(
                lambda: any(v.get('mode') == 'agent' for v in self.window.views.values()), 30
            )
        self.assertEqual(sent[-1]['agent_pacing'], 'turn')
        identity = next(k for k, v in self.window.views.items() if v.get('mode') == 'agent')
        self.assertEqual(self.window.views[identity]['run']['config']['agent']['speed'], 'turn')
        # The workspace header names the pacing beside the decision maker.
        self.until(lambda: 'turn-based' in (self.js('document.body.textContent') or ''))
        self.window.dispatch('session', dict(session=identity, method='play', speed=1))
        started = time.monotonic()
        # A six-second session finishes well before six seconds when the market waits.
        self.until(lambda: self.window.views[identity]['execution']['status'] == 'completed', 20)
        self.assertLess(time.monotonic() - started, 6)
        # The published view follows the owner past 'ending' to its final state.
        self.until(lambda: self.window.views[identity]['pipeline']['state'] == 'completed', 15)
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
