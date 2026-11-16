"""Features migrated from the retired widgets, exercised through the one frontend."""

import json
import os
import time
import unittest
from unittest.mock import patch
from support.desktop import DesktopFixture


@unittest.skipUnless(os.environ.get('TRADING_DESKTOP_TESTS') == '1', 'optional Desktop suite')
class DesktopFeatureTests(DesktopFixture, unittest.TestCase):
    def open_session(self, identity):
        selector = '[data-action="open:' + identity + '"]'
        self.until(lambda: self.js('document.querySelector(' + json.dumps(selector) + ') !== null'))
        self.click(selector)

    def test_market_preview_steps_without_creating_account_or_experiment(self):
        count = len(self.window.catalogue['accounts'])
        self.click('[data-action="data"]')
        self.click('.recording-toggle')
        item = self.window.dataset_catalogue['files'][0]
        self.click('[data-action="preview:' + item['id'] + '"]')
        self.until(
            lambda: (self.window.views['manager'].get('preparation') or {}).get('status') == 'ready'
        )
        self.until(
            lambda: self.js('document.querySelector("[data-action=preview-step]").disabled')
            is False
        )
        before = self.window.views['manager']['market']['time_ns']
        self.click('[data-action="preview-step"]')
        self.until(lambda: self.window.views['manager']['market']['time_ns'] > before)
        self.assertIsNone(self.window.views['manager']['run'])
        self.assertIsNone(self.window.views['manager']['account'])
        self.assertEqual(len(self.window.catalogue['accounts']), count)
        self.assertEqual(self.window.catalogue['runs'], [])
        self.assertEqual(self.js('document.querySelectorAll("[role=tab]").length'), 0)
        self.assertEqual(self.errors, [])

    def test_setup_preserves_announcements_and_complete_ending_option(self):
        self.click('[data-action="new"]')
        self.js(
            "document.querySelector('[name=news]').value=" + json.dumps(str(self.source[1])) + ";"
            "document.querySelector('[name=allow_implicit_eof]').checked=true;"
            "document.querySelector('#setup-form').requestSubmit()"
        )
        self.js("document.querySelector('#setup-form').requestSubmit()")
        self.js("document.querySelector('#setup-form').requestSubmit()")
        self.until(
            lambda: any(k != 'manager' and v.get('run') for k, v in self.window.views.items())
        )
        view = next(v for k, v in self.window.views.items() if k != 'manager' and v.get('run'))
        self.assertEqual(view['source']['news_path'], str(self.source[1].resolve()))
        self.assertTrue(view['source']['allow_implicit_eof'])
        self.assertEqual(self.errors, [])

    def test_scheduled_handoff_fires_and_restore_uses_saved_policy_state(self):
        identity = self.prepare('Scheduled switch', '10000')
        self.open_session(identity)
        notices = []
        self.window.browser.commands.event.connect(
            lambda encoded: notices.append(json.loads(encoded))
        )
        self.click('[data-action="inspector:controls"]')
        self.js(
            "document.querySelector('[name=policy]').value='rule';"
            "document.querySelector('[name=condition]').value='time';"
            "document.querySelector('[name=condition_value]').value='09:30:03.000000001';"
            "document.querySelector('#handoff-form').requestSubmit()"
        )
        self.until(
            lambda: any(e['kind'] == 'notice' and 'scheduled' in e['value'] for e in notices)
        )
        self.assertEqual(self.window.views[identity]['handoffs'], [])
        self.click('[data-action="step"]')
        self.until(lambda: len(self.window.views[identity]['handoffs']) == 1)
        self.js(
            "document.querySelector('[name=policy]').value='human';"
            "document.querySelector('[name=condition]').value='now';"
            "document.querySelector('#handoff-form').requestSubmit()"
        )
        self.until(lambda: len(self.window.views[identity]['handoffs']) == 2)
        self.js(
            "document.querySelector('[name=policy]').value='rule';"
            "document.querySelector('[name=state_mode]').value='restore';"
            "document.querySelector('#handoff-form').requestSubmit()"
        )
        self.until(lambda: len(self.window.views[identity]['handoffs']) == 3)
        self.assertEqual(self.window.views[identity]['handoffs'][-1]['state_mode'], 'restore')
        self.assertEqual(self.errors, [])

    def test_position_condition_waits_for_a_real_fill(self):
        identity = self.prepare('Position switch', '10000')
        self.open_session(identity)
        notices = []
        self.window.browser.commands.event.connect(
            lambda encoded: notices.append(json.loads(encoded))
        )
        self.click('[data-action="inspector:controls"]')
        self.js(
            "document.querySelector('[name=policy]').value='hold';"
            "document.querySelector('[name=condition]').value='position_at_least';"
            "document.querySelector('[name=condition_value]').value='1';"
            "document.querySelector('#handoff-form').requestSubmit()"
        )
        self.until(
            lambda: any(e['kind'] == 'notice' and 'scheduled' in e['value'] for e in notices)
        )
        self.assertEqual(self.window.views[identity]['handoffs'], [])
        self.click('[data-action="inspector:order"]')
        self.js(
            "document.querySelector('[name=quantity]').value='1';"
            "document.querySelector('[name=limit_price]').value='190.10';"
            "document.querySelector('[name=client_id]').value='position-trigger';"
            "document.querySelector('#order-form').requestSubmit()"
        )
        self.until(lambda: bool(self.window.views[identity]['account']['orders']))
        self.click('[data-action="step"]')
        self.until(lambda: bool(self.window.views[identity]['handoffs']))
        self.assertTrue(any(e['kind'] == 'fill' for e in self.window.views[identity]['events']))
        self.assertIn(
            'position-trigger', self.window.views[identity]['account']['orders'][0]['intent_id']
        )
        self.assertEqual(self.errors, [])

    def test_settings_account_can_be_selected_and_not_shared_by_active_experiments(self):
        self.click('[data-action="settings"]')
        self.js(
            "document.querySelector('#account-form [name=name]').value='Reusable account';"
            "document.querySelector('#account-form [name=cash]').value='12345';"
            "document.querySelector('#account-form').requestSubmit()"
        )
        self.until(
            lambda: any(a['name'] == 'Reusable account' for a in self.window.catalogue['accounts'])
        )
        account = next(
            a for a in self.window.catalogue['accounts'] if a['name'] == 'Reusable account'
        )
        self.click('[data-action="new"]')
        self.js("document.querySelector('#setup-form').requestSubmit()")
        self.js(
            "document.querySelector('[name=account]').value=" + json.dumps(account['id']) + ";"
            "document.querySelector('[name=account]').dispatchEvent(new Event('input',{bubbles:true}));"
            "document.querySelector('[name=account]').dispatchEvent(new Event('change',{bubbles:true}))"
        )
        self.js("document.querySelector('#setup-form').requestSubmit()")
        self.js("document.querySelector('#setup-form').requestSubmit()")
        self.until(
            lambda: any(k != 'manager' and v.get('run') for k, v in self.window.views.items())
        )
        view = next(v for k, v in self.window.views.items() if k != 'manager' and v.get('run'))
        self.assertEqual(view['run']['account_id'], account['id'])
        self.assertEqual(view['account']['cash'], 123_450_000)
        self.window.dispatch(
            'prepare',
            dict(
                path=str(self.source[0]),
                symbol='AAPL',
                account=account['id'],
                policy='human',
                execution_mode='controlled',
                fee_per_share='.01',
                latency_ns=0,
                quantity=10,
                hold_ns=2_000_000_000,
            ),
        )
        self.until(lambda: bool(self.errors))
        self.assertIn('open experiment', self.errors[-1])
        self.errors.clear()

    def test_comparison_exports_the_actual_completed_results(self):
        self.click('[data-action="comparison"]')
        self.js("document.querySelector('#comparison-form').requestSubmit()")
        self.until(
            lambda: self.js('document.querySelectorAll("[data-action=export-comparison]").length')
            == 1,
            timeout=30,
        )
        destination = self.path / 'comparison.json'
        with patch(
            'sim_platform.desktop.web.window.QFileDialog.getSaveFileName',
            return_value=(str(destination), ''),
        ):
            self.click('[data-action="export-comparison"]')
            self.until(destination.exists)
        result = json.loads(destination.read_text())
        self.assertTrue(result['results'])
        self.assertEqual(
            self.js('document.querySelectorAll(".form-block table tbody tr").length'),
            len(result['results']),
        )
        self.assertEqual(self.errors, [])

    def test_play_pause_freezes_clock_and_end_releases_reservations(self):
        identity = self.prepare('Pause and stop', '10000')
        self.open_session(identity)
        self.js(
            "document.querySelector('[name=limit_price]').value='100';"
            "document.querySelector('#order-form').requestSubmit()"
        )
        self.until(lambda: self.window.views[identity]['execution']['reserved_cash'] > 0)
        self.click('[data-action="play"]')
        self.until(lambda: self.window.views[identity]['market']['status'] == 'playing')
        self.click('[data-action="pause"]')
        self.until(lambda: self.window.views[identity]['market']['status'] == 'paused')
        stamp = self.window.views[identity]['market']['time_ns']
        end = time.monotonic() + 0.1
        self.until(lambda: time.monotonic() > end)
        self.assertEqual(self.window.views[identity]['market']['time_ns'], stamp)
        self.window.dispatch('session', dict(session=identity, method='stop'))
        self.until(lambda: self.window.views[identity]['execution']['status'] == 'stopped')
        self.assertEqual(self.window.views[identity]['execution']['reserved_cash'], 0)
        self.assertEqual(self.errors, [])

    def test_speed_control_survives_market_toolbar_updates_and_records_show_real_data(self):
        identity = self.prepare('Speed and records', '10000')
        self.open_session(identity)
        self.click('[data-action="step"]')
        self.until(lambda: self.window.views[identity]['market']['time_ns'] > 34_202_000_000_000)
        self.js(
            "document.querySelector('#playback-speed').value='2';"
            "document.querySelector('#playback-speed').dispatchEvent(new Event('change',{bubbles:true}))"
        )
        commands = []
        self.window.owners[identity].command.connect(
            lambda name, args: commands.append((name, args))
        )
        self.click('[data-action="play"]')
        self.until(lambda: any(name == 'play' for name, args in commands))
        self.assertEqual(next(args['speed'] for name, args in commands if name == 'play'), 2)
        self.until(lambda: self.window.views[identity]['market']['status'] == 'playing')
        self.click('[data-action="pause"]')
        self.until(lambda: self.window.views[identity]['market']['status'] == 'paused')
        self.click('[data-action="audit"]')
        self.js(
            "document.querySelector('#audit-kind').value='bars';"
            "document.querySelector('#audit-kind').dispatchEvent(new Event('change'))"
        )
        bars = self.window.views[identity]['bars']
        self.assertTrue(bars)
        self.assertEqual(
            (
                self.js('document.querySelectorAll(".audit-table tbody tr").length')
                if self.js('!!document.querySelector(".audit-table")')
                else self.js('document.querySelectorAll(".content table tbody tr").length')
            ),
            len(bars),
        )
        self.assertIn(
            str(bars[0]['volume']), self.js('document.querySelector(".main").textContent')
        )
        self.assertEqual(self.errors, [])

    def test_live_ready_state_uses_play_and_disables_orders_until_running(self):
        identity = self.prepare('Live state', '10000', execution_mode='live')
        self.open_session(identity)
        self.until(lambda: self.js('document.querySelector("[data-action=play]") !== null'))
        self.assertTrue(self.js('document.querySelector("#order-form .primary").disabled'))
        self.assertEqual(self.window.menus.actions['play-pause'].text(), 'Play')
        self.click('[data-action="play"]')
        self.until(lambda: self.window.views[identity]['pipeline']['state'] == 'running')
        self.until(lambda: not self.js('document.querySelector("#order-form .primary").disabled'))
        self.window.menus.actions['play-pause'].trigger()
        self.until(lambda: self.window.views[identity]['pipeline']['state'] == 'paused')
        self.until(lambda: self.js('document.querySelector("#order-form .primary").disabled'))
        self.assertEqual(self.window.menus.actions['play-pause'].text(), 'Play')
        self.assertEqual(self.errors, [])
