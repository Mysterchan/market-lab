"""The sole Desktop frontend: real accounts, commands, navigation and lifecycle."""

import json
import os
from pathlib import Path
import unittest
from support.desktop import DesktopFixture


@unittest.skipUnless(os.environ.get('TRADING_DESKTOP_TESTS') == '1', 'optional Desktop suite')
class DesktopTests(DesktopFixture, unittest.TestCase):
    def test_two_experiments_have_separate_accounts_orders_and_clocks(self):
        first, second = self.prepare('First', '10000'), self.prepare('Second', '20000')
        a, b = self.window.views[first], self.window.views[second]
        self.assertNotEqual(a['run']['account_id'], b['run']['account_id'])
        self.assertEqual(a['account']['cash'], 100_000_000)
        self.assertEqual(b['account']['cash'], 200_000_000)
        old_time = b['market']['time_ns']
        self.window.dispatch(
            'session',
            dict(
                session=first,
                method='order',
                client_id='web-test',
                side='buy',
                quantity=10,
                limit_price='190.10',
            ),
        )
        self.until(lambda: self.window.views[first]['account']['orders'])
        self.assertEqual(self.window.views[second]['account']['orders'], [])
        self.window.dispatch('session', dict(session=first, method='step'))
        self.until(lambda: self.window.views[first]['market']['time_ns'] != a['market']['time_ns'])
        self.assertEqual(self.window.views[second]['market']['time_ns'], old_time)
        self.assertEqual(self.errors, [])

    def test_management_pages_replace_content_and_data_rows_toggle(self):
        self.click('[data-action="data"]')
        self.until(lambda: self.js('document.querySelectorAll(".recording-toggle").length') > 0)
        self.assertEqual(self.js('document.querySelectorAll("[role=tab]").length'), 0)
        self.click('.recording-toggle')
        self.assertEqual(self.js('document.querySelectorAll(".dataset-detail-row").length'), 1)
        self.click('.recording-toggle')
        self.assertEqual(self.js('document.querySelectorAll(".dataset-detail-row").length'), 0)
        self.click('[data-action="agents"]')
        self.assertEqual(self.js('document.querySelector("h1").textContent'), 'Agents')
        self.assertEqual(self.js('document.querySelectorAll("[role=tab]").length'), 0)

    def test_workspace_tabs_do_not_keep_experiments_page_and_close_keeps_run(self):
        first, second = self.prepare('First', '10000'), self.prepare('Second', '20000')
        self.until(
            lambda: self.js(
                'document.querySelectorAll("[data-action=\\"open:' + first + '\\"]").length'
            )
            > 0
        )
        self.click('[data-action="open:' + first + '"]')
        self.until(lambda: self.js('document.querySelectorAll("[role=tab]").length') == 1)
        self.click('[data-action="experiments"]')
        self.assertEqual(self.js('document.querySelectorAll("[role=tab]").length'), 0)
        self.click('[data-action="open:' + second + '"]')
        self.assertEqual(self.js('document.querySelectorAll("[role=tab]").length'), 2)
        self.js(
            "document.querySelector('[name=limit_price]').value='191.1234';"
            "document.querySelector('[name=limit_price]').dispatchEvent(new Event('input', {bubbles:true}))"
        )
        # A manual run has no Agent activity tab; the decision-maker panel is the other view.
        self.assertEqual(
            self.js('document.querySelectorAll("[data-action=\\"inspector:agent\\"]").length'), 0
        )
        self.click('[data-action="inspector:controls"]')
        self.click('[data-action="open:' + first + '"]')
        self.assertEqual(self.js('document.querySelectorAll("#order-form").length'), 1)
        self.click('[data-action="open:' + second + '"]')
        self.assertEqual(self.js('document.querySelectorAll("#handoff-form").length'), 1)
        self.click('[data-action="inspector:order"]')
        self.assertEqual(self.js('document.querySelector("[name=limit_price]").value'), '191.1234')
        self.click('[data-action="inspector:controls"]')
        self.click('[data-action="close:' + second + '"]')
        self.assertEqual(self.js('document.querySelectorAll("#order-form").length'), 1)
        self.assertEqual(self.js('document.querySelectorAll("[role=tab]").length'), 1)
        self.assertIn(second, self.window.views)
        self.assertTrue(self.window.owners[second].thread.isRunning())

    def test_setup_form_creates_real_run_and_agent_editor_saves_revision(self):
        self.click('[data-action="new"]')
        self.js("document.querySelector('#setup-form').requestSubmit()")
        self.js("document.querySelector('[name=account_name]').value='Form account'")
        self.js("document.querySelector('[name=cash]').value='15000'")
        self.js("document.querySelector('#setup-form').requestSubmit()")
        self.js("document.querySelector('#setup-form').requestSubmit()")
        self.until(
            lambda: any(
                key != 'manager' and view.get('run') for key, view in self.window.views.items()
            )
        )
        self.until(lambda: self.js('document.querySelectorAll("[role=tab]").length') == 1)
        identity = next(key for key in self.window.views if key != 'manager')
        self.assertEqual(self.window.views[identity]['account']['cash'], 150_000_000)
        self.click('[data-action="agents"]')
        self.click('[data-action="agent-new"]')
        self.js("document.querySelector('[name=name]').value='Web reader'")
        self.js("document.querySelector('[name=prompt]').value='Read the market quote'")
        self.js("document.querySelector('#agent-form').requestSubmit()")
        self.until(lambda: self.window.agent_catalogue.get('agents'))
        saved = self.window.agent_catalogue['agents'][0]
        self.assertEqual(saved['definition']['name'], 'Web reader')
        self.assertEqual(saved['revision'], 1)
        self.until(lambda: self.js('document.querySelectorAll(".agent-card").length') == 1)
        self.assertIn(
            'Read the market quote',
            self.js('document.querySelector(".agent-description").textContent'),
        )
        self.assertEqual(self.errors, [])

    def test_agent_editor_saves_a_local_model_server(self):
        self.click('[data-action="agents"]')
        self.click('[data-action="agent-new"]')
        self.assertEqual(self.js('document.querySelectorAll("[name=base_url]").length'), 0)
        self.js(
            "const p=document.querySelector('[name=provider]');p.value='openai-compatible';"
            "p.dispatchEvent(new Event('input',{bubbles:true}));"
            "p.dispatchEvent(new Event('change',{bubbles:true}))"
        )
        self.until(lambda: self.js('document.querySelectorAll("[name=base_url]").length') == 1)
        self.js(
            "document.querySelector('[name=name]').value='Local reader';"
            "document.querySelector('[name=model]').value='qwen2.5:7b';"
            "document.querySelector('[name=credential_ref]').value='';"
            "document.querySelector('[name=base_url]').value='http://127.0.0.1:8000/v1';"
            "document.querySelector('[name=prompt]').value='Read the market quote';"
            "document.querySelector('#agent-form').requestSubmit()"
        )
        self.until(lambda: self.window.agent_catalogue.get('agents'))
        saved = self.window.agent_catalogue['agents'][0]
        model = saved['definition']['parameters']['desktop_model']
        self.assertEqual(model['provider'], 'openai-compatible')
        self.assertEqual(model['base_url'], 'http://127.0.0.1:8000/v1')
        self.assertEqual(saved['definition']['model_profile_ref'], 'local-model')
        self.assertEqual(self.errors, [])

    def test_import_failure_keeps_page_editable_and_saved_results_do_not_steal_navigation(self):
        self.window.dispatch('add_dataset', dict(path=str(self.path / 'missing.bin'), copy=False))
        self.until(lambda: bool(self.errors))
        self.click('[data-action="data"]')
        self.assertEqual(self.js('document.querySelector("h1").textContent'), 'Market data')
        identity = self.prepare('Archived', '10000')
        self.window.dispatch('session', dict(session=identity, method='stop'))
        self.until(lambda: self.window.views[identity]['execution']['status'] != 'running')
        self.window.dispatch('open_saved', dict(run_id=self.window.views[identity]['run']['id']))
        self.until(lambda: self.window.views['manager'].get('mode') == 'saved')
        self.click('[data-action="data"]')
        self.window.dispatch('refresh', {})
        # The saved result's repeated snapshot must not navigate away from Market data.
        self.until(lambda: self.js('document.querySelector("h1").textContent') == 'Market data')
        for _ in range(5):
            self.qt.processEvents()
        self.assertEqual(self.js('document.querySelector("h1").textContent'), 'Market data')

    def test_market_updates_preserve_chart_ticket_and_input_focus(self):
        identity = self.prepare('Stable editor', '10000')
        self.until(
            lambda: self.js(
                'document.querySelector('
                + json.dumps('[data-action="open:' + identity + '"]')
                + ') !== null'
            )
        )
        self.click('[data-action="open:' + identity + '"]')
        self.js("document.querySelector('#order-form').setAttribute('data-probe','mounted')")
        self.js("document.querySelector('.m-price-chart').setAttribute('data-probe','mounted')")
        self.js(
            "const input=document.querySelector('#limit_price');input.value='190.1234';input.dispatchEvent(new Event('input',{bubbles:true}));input.focus()"
        )
        old_time = self.window.views[identity]['market']['time_ns']
        self.window.dispatch('session', dict(session=identity, method='step'))
        self.until(lambda: self.window.views[identity]['market']['time_ns'] != old_time)
        from sim_platform.desktop.presentation import clock

        expected_time = (
            clock(self.window.views[identity]['market']['time_ns']).split('.')[0] + ' ET'
        )
        self.until(
            lambda: self.js('document.querySelector(".footer .push").textContent') == expected_time
        )
        self.assertEqual(self.js('document.querySelector("#order-form").dataset.probe'), 'mounted')
        self.assertEqual(
            self.js('document.querySelector(".m-price-chart").dataset.probe'), 'mounted'
        )
        self.assertEqual(self.js('document.activeElement.id'), 'limit_price')
        self.assertEqual(self.js('document.querySelector("#limit_price").value'), '190.1234')
        if self.window.views[identity].get('bars'):
            self.assertTrue(self.js('document.querySelector(".chart-empty").hidden'))
        self.assertEqual(self.errors, [])

    def test_reference_setup_preserves_values_between_steps_and_has_summary(self):
        self.click('[data-action="new"]')
        self.assertEqual(
            self.js('document.querySelector(".step.active").textContent').strip(), '1Market'
        )
        self.js(
            "document.querySelector('#symbol').value='MSFT';document.querySelector('#symbol').dispatchEvent(new Event('input',{bubbles:true}))"
        )
        self.js("document.querySelector('#setup-form').requestSubmit()")
        self.js(
            "document.querySelector('#cash').value='12345';document.querySelector('#cash').dispatchEvent(new Event('input',{bubbles:true}))"
        )
        self.click('[data-action="setup-back"]')
        self.assertEqual(self.js('document.querySelector("#symbol").value'), 'MSFT')
        self.js("document.querySelector('#setup-form').requestSubmit()")
        self.assertEqual(self.js('document.querySelector("#cash").value'), '12345')
        self.assertIn('$12,345.00', self.js('document.querySelector(".summary").textContent'))
        self.assertEqual(self.js('document.querySelectorAll("[role=tab]").length'), 0)

    def test_completed_run_opens_read_only_report_without_trading_controls(self):
        identity = self.prepare('Report account', '10000')
        self.window.dispatch('session', dict(session=identity, method='stop'))
        self.until(lambda: self.window.views[identity]['execution']['status'] != 'running')
        run_id = self.window.views[identity]['run']['id']
        selector = '[data-action="saved:' + run_id + '"]'
        self.until(lambda: self.js('document.querySelector(' + json.dumps(selector) + ') !== null'))
        self.click(selector)
        self.until(lambda: self.js('document.querySelectorAll(".result-grid").length') == 1)
        self.assertEqual(self.js('document.querySelectorAll("#order-form,[role=tab]").length'), 0)
        self.assertIn('Report account', self.js('document.querySelector("h1").textContent'))
        self.assertIn(
            'Experiment ended', self.js('document.querySelector(".timeline").textContent')
        )
        self.click('[data-action="data"]')
        self.window.dispatch('refresh', {})
        self.until(lambda: self.js('document.querySelector("h1").textContent') == 'Market data')
        self.assertEqual(self.errors, [])

    def test_native_menus_route_actions_and_quit_cancellation_keeps_workers_alive(self):
        from unittest.mock import patch
        from PySide6.QtCore import QEvent

        menus = self.window.menus
        self.assertFalse(menus.actions['step'].isEnabled())
        menus.actions['data'].trigger()
        self.until(lambda: self.js('document.querySelector("h1").textContent') == 'Market data')
        menus.actions['new'].trigger()
        self.until(lambda: self.js('document.querySelectorAll("#setup-form").length') == 1)
        identity = self.prepare('Menu session', '10000')
        self.click('[data-action="experiments"]')
        self.click('[data-action="open:' + identity + '"]')
        self.until(lambda: menus.actions['step'].isEnabled())
        old_time = self.window.views[identity]['market']['time_ns']
        menus.actions['step'].trigger()
        self.until(lambda: self.window.views[identity]['market']['time_ns'] != old_time)
        with patch.object(self.window, 'confirm_close', return_value=False):
            self.qt.sendEvent(self.qt, QEvent(QEvent.Type.Quit))
        self.assertFalse(self.window._closing)
        self.assertTrue(self.window.owners[identity].thread.isRunning())
        menus.actions['close-tab'].trigger()
        self.until(lambda: self.js('document.querySelector("h1").textContent') == 'Experiments')
        self.assertEqual(self.window.views[identity]['execution']['status'], 'running')

    def test_settings_storage_and_reset_are_guarded_and_leave_window_usable(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox

        target = self.path / 'managed-data'
        target.mkdir()
        with patch(
            'sim_platform.desktop.web.window.QFileDialog.getExistingDirectory',
            return_value=str(target),
        ):
            self.window.dispatch('dataset_storage', {})
        self.until(
            lambda: self.window.dataset_catalogue.get('storage_root') == str(target.resolve())
        )
        identity = self.prepare('Reset session', '10000')
        with patch('sim_platform.desktop.web.window.QMessageBox.warning') as warning:
            self.window.dispatch('reset_local_data', {})
            warning.assert_not_called()
        self.assertIn('End experiments', self.errors[-1])
        self.errors.clear()
        self.window.dispatch('session', dict(session=identity, method='stop'))
        self.until(lambda: self.window.views[identity]['execution']['status'] != 'running')
        with patch(
            'sim_platform.desktop.web.window.QMessageBox.warning',
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window.dispatch('reset_local_data', {})
        self.until(lambda: len(self.window.owners) == 1 and not self.window.catalogue.get('runs'))
        self.until(lambda: self.js('document.querySelector("h1").textContent') == 'Experiments')
        self.assertTrue(self.source[0].is_file())
        self.assertTrue(self.window.manager.thread.isRunning())
        self.assertEqual(self.errors, [])

    def test_renderer_recovery_and_small_windows_preserve_navigation(self):
        self.window._renderer_failed()
        self.assertIs(self.window.pages.currentWidget(), self.window.failure_page)
        loaded = []
        self.window.browser.loadFinished.connect(loaded.append)
        self.window._reload_frontend()
        self.until(lambda: loaded and loaded[-1])
        self.until(lambda: self.js('document.querySelectorAll(".nav").length') > 0)
        self.click('[data-action="agents"]')
        self.click('[data-action="agent-new"]')
        for width, height in ((1000, 700), (1360, 900)):
            self.window.resize(width, height)
            self.until(lambda: self.js('document.querySelector("main").clientWidth') > 0)
            self.assertTrue(self.js('document.documentElement.scrollWidth <= innerWidth'))
            self.assertTrue(
                self.js(
                    'document.querySelector(".form-actions").getBoundingClientRect().right <= innerWidth'
                )
            )
        self.assertEqual(self.errors, [])

    def test_cancelled_preparation_allows_a_fresh_experiment(self):
        from unittest.mock import patch
        from sim_platform.domain.models import ContractError
        from sim_platform.desktop.workflows.experiments import ExperimentPreparation
        from threading import Event

        entered = Event()

        def blocked(instance, *, arguments, progress, cancellation):
            entered.set()
            cancellation._event.wait(10)
            raise ContractError('Experiment preparation cancelled')

        arguments = dict(
            path=str(self.source[0]),
            symbol='AAPL',
            account_name='Cancel me',
            cash='10000',
            policy='human',
            execution_mode='controlled',
            fee_per_share='.01',
            latency_ns=0,
            quantity=10,
            hold_ns=2_000_000_000,
            start_mode='first_book',
        )
        with patch.object(ExperimentPreparation, 'prepare', blocked):
            self.window.dispatch('prepare', arguments)
            self.until(entered.is_set)
            self.window.dispatch('cancel_preparation', {})
            self.until(lambda: not self.window.preparing)
            self.until(lambda: bool(self.errors))
        self.assertIn('cancelled', self.errors[-1])
        self.errors.clear()
        identity = self.prepare('After cancellation', '10000')
        self.assertEqual(self.window.views[identity]['execution']['status'], 'running')
        self.assertEqual(self.errors, [])

    def test_configured_agent_runs_and_recorded_replay_uses_no_new_model_calls(self):
        from unittest.mock import patch
        from sim_platform import Platform
        from sim_platform.examples.configured_agent_demo import QuoteOnlyProvider

        self.click('[data-action="agents"]')
        self.click('[data-action="agent-new"]')
        self.js(
            "document.querySelector('[name=name]').value='Offline reader';"
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
            self.js(
                "document.querySelector('[name=end_time]').value='09:30:06';"
                "document.querySelector('[name=timeout_seconds]').value='17';"
                "document.querySelector('#setup-form').requestSubmit()"
            )
            sent = []
            self.window.browser.commands.requested.connect(
                lambda name, args: sent.append(args) if name == 'prepare' else None
            )
            self.js("document.querySelector('#setup-form').requestSubmit()")
            self.until(
                lambda: any(v.get('mode') == 'agent' for v in self.window.views.values()), 30
            )
        self.assertEqual(sent[-1]['end_ns'], 34_206_000_000_000)
        self.assertEqual(sent[-1]['timeout_seconds'], 17)
        identity = next(k for k, v in self.window.views.items() if v.get('mode') == 'agent')
        self.window.dispatch('session', dict(session=identity, method='play', speed=1))
        self.until(
            lambda: bool(self.window.views[identity].get('activity'))
            or self.window.views[identity]['pipeline']['state'] == 'ended'
        )
        self.window.dispatch('session', dict(session=identity, method='stop'))
        self.until(lambda: self.window.views[identity]['execution']['status'] != 'running')
        run_id = self.window.views[identity]['run']['id']
        log = next((self.path / 'accounts').rglob(run_id + '.jsonl'))
        verified = []

        def replay_result(encoded):
            event = json.loads(encoded)
            if event['kind'] == 'replay':
                verified.append(event['value'])

        self.window.browser.commands.event.connect(replay_result)
        self.window.dispatch('prepare_replay', dict(path=str(log)))
        self.until(lambda: bool(verified), 25)
        self.assertTrue(verified[0]['matched'])
        self.assertTrue(verified[0]['events_verified'])
        self.assertEqual(verified[0]['model_calls'], 0)
        self.click('[data-action="replay"]')
        self.until(lambda: self.js('document.querySelectorAll(".result-banner").length') > 0)
        self.assertIn('verified', self.js('document.querySelector(".result-banner").textContent'))
        self.assertEqual(self.errors, [])

    def test_import_form_adds_managed_copy_and_returns_to_library(self):
        from unittest.mock import patch

        self.window.menus.actions['import'].trigger()
        self.until(lambda: self.js('document.querySelectorAll("#import-form").length') == 1)
        with patch(
            'sim_platform.desktop.web.window.QFileDialog.getOpenFileName',
            return_value=(str(self.source[0]), ''),
        ):
            self.click('[data-action="pick:recording"]')
        self.until(
            lambda: self.js('document.querySelector("#import-form .primary").disabled') is False
        )
        self.js(
            "document.querySelector('[name=copy][value=true]').click();"
            "document.querySelector('#import-form').requestSubmit()"
        )
        self.until(
            lambda: any(row['managed'] for row in self.window.dataset_catalogue.get('files', []))
        )
        self.until(lambda: self.js('document.querySelector("h1").textContent') == 'Market data')
        copy = next(row for row in self.window.dataset_catalogue['files'] if row['managed'])
        self.assertEqual(Path(copy['path']).read_bytes(), self.source[0].read_bytes())
        self.assertEqual(self.errors, [])

    def test_sdk_export_from_native_menu_writes_installable_wheel(self):
        from unittest.mock import patch
        from sim_platform.sdk.distribution import FILENAME
        import zipfile

        destination = self.path / FILENAME
        with patch(
            'sim_platform.desktop.web.window.QFileDialog.getSaveFileName',
            return_value=(str(destination), ''),
        ):
            self.window.menus.actions['export-sdk'].trigger()
            # The worker writes the file in place; wait for a complete archive, not just
            # for the file to appear, or a busy machine can read it half-written.
            self.until(lambda: destination.exists() and zipfile.is_zipfile(destination))
        with zipfile.ZipFile(destination) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn('sim_platform/sdk/__init__.py', archive.namelist())
            self.assertFalse(any('desktop/' in name for name in archive.namelist()))
        self.assertEqual(self.errors, [])

    def test_saved_agent_library_is_loaded_again_after_reopening_app(self):
        from unittest.mock import patch
        from sim_platform.desktop.app import open_window
        from PySide6.QtCore import QCoreApplication, QEvent
        from shiboken6 import isValid

        self.click('[data-action="agents"]')
        self.click('[data-action="agent-new"]')
        self.js(
            "document.querySelector('[name=name]').value='Persisted reader';"
            "document.querySelector('[name=prompt]').value='Read the market quote';"
            "document.querySelector('#agent-form').requestSubmit()"
        )
        self.until(lambda: self.window.agent_catalogue.get('agents'))
        old = self.window
        with patch.object(old, 'confirm_close', return_value=True):
            old.close()
        self.until(lambda: all(not o.thread.isRunning() for o in old.owners.values()))
        self.qt.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.until(lambda: not isValid(old))
        self.window = open_window(self.path / 'accounts')
        self.window.browser.commands.event.connect(self.capture)
        self.window.browser.page().failed.connect(self.errors.append)
        self.window.show()
        self.until(lambda: bool(self.window.agent_catalogue.get('agents')))
        self.until(lambda: self.js('document.querySelectorAll(".nav").length') > 0)
        self.click('[data-action="agents"]')
        self.until(lambda: self.js('document.querySelectorAll(".agent-card").length') == 1)
        self.assertIn(
            'Persisted reader', self.js('document.querySelector(".agent-card").textContent')
        )
        self.assertEqual(self.errors, [])

    def test_bridge_rejects_arbitrary_and_non_object_commands(self):
        from sim_platform.desktop.web.frontend_bridge import FrontendCommands

        commands = FrontendCommands()
        received = []
        commands.requested.connect(lambda name, args: received.append((name, args)))
        commands.invoke('eval', '{}')
        commands.invoke('session', '[]')
        commands.invoke('session', 'invalid')
        commands.invoke('session', 'x' * 262145)
        self.assertEqual(received, [])
        commands.invoke('refresh', '{}')
        self.assertEqual(received, [('refresh', {})])
