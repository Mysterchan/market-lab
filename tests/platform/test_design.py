"""Legible active color states and shared renderer token contracts."""

from pathlib import Path
import sys
import unittest

from sim_platform.design import palette

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from build_pages import design_stylesheet


def contrast(first, second):
    def luminance(color):
        channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
        return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


class DesignTests(unittest.TestCase):
    def test_active_text_and_primary_hover_remain_readable_in_both_themes(self):
        for light in (True, False):
            colors = palette(light)
            for foreground in ('text', 'muted', 'accent'):
                for background in ('background', 'surface', 'selected'):
                    with self.subTest(light=light, text=foreground, surface=background):
                        self.assertGreaterEqual(
                            contrast(colors[foreground], colors[background]), 4.5
                        )
            for background in ('primary', 'primary_hover'):
                self.assertGreaterEqual(contrast(colors['on_primary'], colors[background]), 4.5)
            for foreground in ('on_brand', 'brand_secondary'):
                self.assertGreaterEqual(contrast(colors[foreground], colors['brand_surface']), 4.5)

    def test_renderers_receive_the_same_semantic_values(self):
        css = design_stylesheet()
        for light in (True, False):
            colors = palette(light)
            for name, value in colors.items():
                self.assertIn('--ml-' + name.replace('_', '-') + ': ' + value + ';', css)
        detached = palette(True)
        detached['text'] = '#ffffff'
        self.assertNotEqual(palette(True)['text'], detached['text'])
