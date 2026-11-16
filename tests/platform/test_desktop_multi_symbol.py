"""An experiment on several symbols, created and traded through the real Desktop."""

import json
import os
import unittest
from support.desktop import DesktopFixture

AAPL = 'date,open,high,low,close,volume\n' + ''.join(
    f'2026-01-{d:02d},{100 + d}.00,{101 + d}.00,{99 + d}.00,{100 + d}.50,100000\n'
    for d in range(5, 12)
)
MSFT = 'date,open,high,low,close,volume\n' + ''.join(
    f'2026-01-{d:02d},50.00,51.00,49.00,50.50,100000\n' for d in range(6, 12)
)


@unittest.skipUnless(os.environ.get('TRADING_DESKTOP_TESTS') == '1', 'optional Desktop suite')
class DesktopMultiSymbolTests(DesktopFixture, unittest.TestCase):
    def set(self, name, value):
        self.js(
            "{const e=document.querySelector('[name="
            + name
            + "]'); e.value="
            + json.dumps(value)
            + "; e.dispatchEvent(new Event('input',{bubbles:true}));}; 1"
        )

    def step(self, identity, count):
        for _ in range(count):
            view = self.window.views[identity]
            if view['execution']['status'] != 'running':
                return
            sequence = view['market']['sequence']
            self.window.dispatch('session', dict(session=identity, method='step'))
            self.until(
                lambda: self.window.views[identity]['market']['sequence'] != sequence
                or self.window.views[identity]['execution']['status'] != 'running'
            )

    def test_two_symbols_from_the_form_to_the_result_page(self):
        for name, text in (('aapl.csv', AAPL), ('msft.csv', MSFT)):
            (self.path / name).write_text(text)
            count = len(self.window.dataset_catalogue.get('files', []))
            self.window.dispatch('add_dataset', dict(path=str(self.path / name), copy=False))
            self.until(lambda: len(self.window.dataset_catalogue.get('files', [])) > count)
        paths = {f['name']: f['path'] for f in self.window.dataset_catalogue['files']}
        self.click('[data-action="new"]')
        self.until(lambda: self.js('document.querySelectorAll(".add-leg").length') == 1)
        self.set('path', paths['aapl.csv'])
        self.set('symbol', 'AAPL')
        self.click('.add-leg')
        self.until(lambda: self.js('document.querySelectorAll("[name=more_symbol_0]").length'))
        self.set('more_path_0', paths['msft.csv'])
        self.set('more_symbol_0', 'MSFT')
        # Only methods that can trade several symbols are offered.
        offered = self.js(
            'JSON.stringify([...document.querySelectorAll("input[name=policy]")].map(e=>e.value))'
        )
        self.assertEqual(json.loads(offered), ['human', 'model'])
        before = set(self.window.views)
        for _ in range(3):
            self.js("document.querySelector('#setup-form').requestSubmit(); 1")
        self.until(
            lambda: any(k not in before and v.get('run') for k, v in self.window.views.items()),
            60,
        )
        identity = next(k for k in self.window.views if k not in before)
        self.assertEqual(self.window.views[identity]['run']['universe'], ['AAPL', 'MSFT'])
        selector = '[data-action="open:' + identity + '"]'
        self.click('[data-action="experiments"]')
        self.until(lambda: self.js('document.querySelector(' + json.dumps(selector) + ') !== null'))
        self.click(selector)
        self.until(lambda: self.js('document.querySelectorAll(".symbol-switch").length') == 1)
        self.step(identity, 8)
        # The ticket follows the switch: this order is for MSFT.
        self.click('[data-action="symbol:MSFT"]')
        self.assertEqual(
            self.js('document.querySelector(".symbol-switch .active").textContent'), 'MSFT'
        )
        self.set('quantity', '20')
        self.set('limit_price', '60')
        self.js("document.querySelector('#order-form').requestSubmit(); 1")
        self.until(lambda: self.window.views[identity]['account']['orders'])
        self.step(identity, 20)
        positions = self.window.views[identity]['account']['positions']
        self.assertEqual([(p['symbol'], p['quantity']) for p in positions], [('MSFT', 20)])
        run_id = self.window.views[identity]['run']['id']
        self.window.dispatch('session', dict(session=identity, method='stop'))
        self.until(lambda: self.window.views[identity]['execution']['status'] != 'running')
        self.click('[data-action="experiments"]')
        saved = '[data-action="saved:' + run_id + '"]'
        self.until(lambda: self.js('document.querySelector(' + json.dumps(saved) + ') !== null'))
        self.click(saved)
        self.until(lambda: self.js('document.querySelectorAll(".result-grid").length') == 1)
        self.assertEqual(self.js('document.querySelectorAll(".symbol-switch button").length'), 2)
        self.assertNotIn('${', self.js('document.querySelector(".result-grid").innerText'))
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
