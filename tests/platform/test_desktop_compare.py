"""The Compare runs page: group saved runs, evaluate them and show the result."""

import json
import os
import unittest

from support.desktop import DesktopFixture


@unittest.skipUnless(os.environ.get('TRADING_DESKTOP_TESTS') == '1', 'optional Desktop suite')
class DesktopCompareTests(DesktopFixture, unittest.TestCase):
    def seed_runs(self):
        """Two rule settings, two runs each, saved in the Desktop's researcher profile."""
        from sim_platform import Platform

        profile = self.window.catalogue['profile']
        app = Platform(self.path / 'accounts')
        try:
            with app.open_batch() as batch:
                for hold in (1, 1, 2, 2):
                    batch.evaluate(
                        self.source[0],
                        symbol='AAPL',
                        hold_ns=hold * 1_000_000_000,
                        profile=profile,
                        include_details=False,
                    )
        finally:
            app.close()

    def open_page(self):
        self.click('[data-action="comparison"]')
        self.until(lambda: self.js('document.querySelectorAll("[data-eval-group]").length') >= 2)

    def test_groups_runs_and_evaluates_against_a_baseline(self):
        self.seed_runs()
        self.open_page()
        labels = json.loads(
            self.js(
                'JSON.stringify([...document.querySelectorAll("[data-eval-group]")]'
                '.map(e => e.dataset.evalGroup))'
            )
        )
        self.assertIn('Rule · buy-hold-sell · hold 1s, 10 shares', labels)
        self.assertIn('Rule · buy-hold-sell · hold 2s, 10 shares', labels)
        for label in labels[:2]:
            self.js(
                '{ const box = [...document.querySelectorAll("[data-eval-group]")]'
                f'.find(e => e.dataset.evalGroup === {label!r}); box.click(); }} true'
            )
        self.until(
            lambda: self.js('document.querySelectorAll("[data-eval-run]:checked").length') == 4
        )
        self.js(
            '{ const s = document.querySelector("[data-eval-baseline]");'
            f's.value = {labels[0]!r}; s.dispatchEvent(new Event("change")); }} true'
        )
        self.click('[data-action="run-evaluation"]')
        self.until(lambda: self.js('document.querySelectorAll(".eval-report").length') == 1)
        rows = json.loads(
            self.js(
                'JSON.stringify([...document.querySelectorAll(".eval-report table")[0]'
                '.querySelectorAll("tbody tr")].map(r => r.textContent))'
            )
        )
        self.assertEqual(len(rows), 2)
        self.assertTrue(any('Baseline' in row for row in rows))
        # A rule repeats exactly, so both groups are constant and no test applies.
        self.assertTrue(any('Exact · no randomness' in row for row in rows))
        self.assertEqual(
            self.js('document.querySelectorAll("[data-action=export-evaluation]").length'), 1
        )
        self.assertEqual(self.errors, [])

    def test_an_empty_workspace_explains_what_to_do(self):
        self.open_empty()
        self.assertIn(
            'No finished runs yet', self.js('document.querySelector(".content").textContent')
        )
        # The earlier rule comparison remains available below.
        self.assertEqual(self.js('document.querySelectorAll("#comparison-form").length'), 1)

    def open_empty(self):
        self.click('[data-action="comparison"]')
        self.until(
            lambda: self.js(
                'document.querySelector(".content")?.textContent.includes("No finished runs yet")'
            )
        )


if __name__ == '__main__':
    unittest.main()
