"""Synthetic checks of the accepted watermark reduction, not a new cleaner."""
import importlib.util
from pathlib import Path
import unittest

import numpy as np
from PIL import Image, ImageDraw

SPEC = importlib.util.spec_from_file_location('accepted_cleaning', Path(__file__).resolve().parents[1] / 'question_boundaries.py')
cropper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cropper)


class WatermarkCompatibilityTests(unittest.TestCase):
    def source(self):
        image = Image.new('RGB', (300, 200), 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((120, 20, 200, 40), fill=(210, 210, 210))
        draw.rectangle((30, 70, 60, 80), fill='black')
        draw.ellipse((100, 90, 150, 140), outline='black', width=2)
        draw.line((100, 115, 150, 115), fill='black', width=2)
        draw.rectangle((200, 100, 240, 120), fill='red')
        return image

    def test_faint_watermark_reduced_while_black_text_and_diagram_remain(self):
        source = self.source()
        original = np.array(source)
        cleaned, changed = cropper.clean_image(source, remove_color=False)
        result = np.array(cleaned)
        self.assertGreater(changed, 0)
        self.assertTrue((result[25:35, 125:195] == 255).all())
        black = (original == 0).all(axis=2)
        self.assertTrue((result[black] == 0).all())
        self.assertEqual(cleaned.size, source.size)
        self.assertTrue((np.array(source) == original).all())

    def test_color_setting_preserves_or_removes_colored_strokes_explicitly(self):
        source = self.source()
        retained, _ = cropper.clean_image(source, remove_color=False)
        removed, _ = cropper.clean_image(source, remove_color=True)
        self.assertEqual(retained.getpixel((220, 110)), (255, 0, 0))
        self.assertEqual(removed.getpixel((220, 110)), (255, 255, 255))
        self.assertEqual(removed.getpixel((40, 75)), (0, 0, 0))
