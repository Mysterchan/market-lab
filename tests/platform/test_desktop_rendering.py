"""Rendering contracts for the one HTML Desktop, using real worker views."""

import copy
import json
import os
import re
import unittest
from support.desktop import DesktopFixture


@unittest.skipUnless(os.environ.get('TRADING_DESKTOP_TESTS') == '1', 'optional Desktop suite')
class DesktopRenderingTests(DesktopFixture, unittest.TestCase):
    def open_session(self):
        identity = self.prepare('Rendering check', '10000')
        selector = '[data-action="open:' + identity + '"]'
        self.until(lambda: self.js('document.querySelector(' + json.dumps(selector) + ') !== null'))
        self.click(selector)
        return identity

    def test_agent_and_account_text_cannot_become_markup(self):
        identity = self.open_session()
        self.window.session_names[identity] = '<img src=x onerror="window.untrusted=true">'
        self.window._view(identity, copy.deepcopy(self.window.views[identity]))
        self.until(
            lambda: '<img' in self.js('document.querySelector(".run-toolbar h1").textContent')
        )
        self.assertEqual(self.js('document.querySelectorAll(".run-toolbar img").length'), 0)
        self.assertFalse(self.js('window.untrusted === true'))
        self.assertEqual(self.errors, [])

    def test_record_counters_are_readable_in_light_and_dark(self):
        from test_design import contrast

        self.open_session()
        for appearance in ('light', 'dark'):
            self.click('[data-action="settings"]')
            self.click('[data-action="' + appearance + '"]')
            self.click('[data-action="workspace"]')
            self.click('[data-action="records:positions"]')
            values = json.loads(
                self.js(
                    '''JSON.stringify([...document.querySelectorAll('.counter')].map(n=>{
                const c=getComputedStyle(n); return [c.color,c.backgroundColor];
            }))'''
                )
            )
            for foreground, background in values:

                def hex_color(value):
                    return '#' + ''.join(f'{int(c):02x}' for c in re.findall(r'\d+', value)[:3])

                with self.subTest(
                    appearance=appearance, foreground=foreground, background=background
                ):
                    self.assertGreaterEqual(
                        contrast(hex_color(foreground), hex_color(background)), 4.5
                    )
        self.assertEqual(self.errors, [])

    def test_missing_bid_does_not_display_invented_portfolio_profit(self):
        identity = self.open_session()
        view = copy.deepcopy(self.window.views[identity])
        view['account']['positions'] = [dict(symbol='AAPL', quantity=1, cost=1_900_000)]
        view['market']['bids'] = []
        self.window._view(identity, view)
        self.until(
            lambda: self.js('document.querySelector(".stats .stat strong").textContent') == '—'
        )
        self.assertEqual(
            self.js('document.querySelectorAll(".stats .stat strong")[1].textContent'), '—'
        )
        self.assertEqual(self.errors, [])
