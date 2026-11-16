"""Layout fixes from the Desktop UI audit, checked in the real frontend."""

import json
import os
import re
import unittest
from support.desktop import DesktopFixture


@unittest.skipUnless(os.environ.get('TRADING_DESKTOP_TESTS') == '1', 'optional Desktop suite')
class DesktopLayoutTests(DesktopFixture, unittest.TestCase):
    def test_avatar_uses_the_researchers_initial(self):
        name = self.js('document.querySelector(".profile strong").textContent')
        self.assertEqual(self.js('document.querySelector(".avatar").textContent'), name[0].upper())

    def test_a_methods_settings_appear_directly_under_its_card(self):
        self.click('[data-action="new"]')
        self.click('input[name=policy][value=model]')
        self.until(lambda: self.js('document.querySelectorAll("[name=model_file]").length') == 1)
        following = self.js(
            "document.querySelector('input[name=policy][value=model]')"
            ".closest('label').nextElementSibling.querySelector('[name=model_file]') !== null"
        )
        self.assertTrue(following)
        self.assertEqual(self.errors, [])

    def test_comparison_has_no_hold_field_it_would_ignore(self):
        self.click('[data-action="comparison"]')
        self.until(lambda: self.js('document.querySelectorAll("#comparison-form").length') == 1)
        self.assertEqual(self.js('document.querySelectorAll("[name=hold_seconds]").length'), 0)
        self.assertIn('1 second', self.js('document.querySelector(".compare-intro").textContent'))

    def test_library_details_name_the_file_type(self):
        self.click('[data-action="data"]')
        self.click('.recording-toggle')
        details = self.js('document.querySelector(".dataset-details").textContent')
        self.assertIn('ITCH 5.0 messages', details)

    def test_preview_prices_use_two_decimals_and_the_page_has_side_margins(self):
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
        self.click('[data-action="preview-step"]')
        self.until(lambda: self.js('document.querySelectorAll(".m-price-chart text").length') > 0)
        labels = json.loads(
            self.js(
                'JSON.stringify([...document.querySelectorAll(".m-price-chart text")]'
                '.map(t => t.textContent))'
            )
        )
        prices = [label for label in labels if re.fullmatch(r'\d+\.\d+', label)]
        self.assertTrue(prices)
        self.assertTrue(all(re.fullmatch(r'\d+\.\d{2}', label) for label in prices), prices)
        padding = self.js('getComputedStyle(document.querySelector(".content")).paddingLeft')
        self.assertEqual(padding, '22px')
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
