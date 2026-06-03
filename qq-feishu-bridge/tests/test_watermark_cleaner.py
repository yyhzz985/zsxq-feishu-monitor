import os
import tempfile
import unittest

import cv2
import numpy as np

from utils.watermark_cleaner import (
    WatermarkCleanError,
    build_pink_watermark_mask,
    remove_pink_watermark,
)


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_DIR = os.path.join(ROOT_DIR, "tests", "fixtures")
SAMPLE_NAMES = [
    "\u6d4b\u8bd5\u53bb\u6c34\u53702.png",
    "\u6d4b\u8bd5\u53bb\u6c34\u53703.png",
    "\u6d4b\u8bd5\u53bb\u6c34\u53704.png",
]


def _read_image(path):
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise AssertionError("failed to read image: %s" % path)
    return image


class WatermarkCleanerTest(unittest.TestCase):
    def test_sample_watermark_images_are_cleaned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for sample_name in SAMPLE_NAMES:
                sample_path = os.path.join(FIXTURE_DIR, sample_name)
                output_path = os.path.join(temp_dir, sample_name + ".cleaned.png")

                result_path = remove_pink_watermark(sample_path, output_path)

                original_mask = build_pink_watermark_mask(_read_image(sample_path))
                cleaned_mask = build_pink_watermark_mask(_read_image(result_path))
                self.assertTrue(os.path.exists(result_path))
                self.assertLess(cleaned_mask.mean(), original_mask.mean() * 0.45)

    def test_plain_image_is_rejected(self):
        image = np.full((300, 500, 3), 255, dtype=np.uint8)
        cv2.putText(image, "plain", (80, 160), cv2.FONT_HERSHEY_SIMPLEX, 2, (30, 30, 30), 4)

        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = os.path.join(temp_dir, "plain.png")
            output_path = os.path.join(temp_dir, "cleaned.png")
            cv2.imwrite(input_path, image)

            with self.assertRaises(WatermarkCleanError):
                remove_pink_watermark(input_path, output_path)

    def test_strong_red_chart_line_is_not_watermark(self):
        image = np.full((300, 500, 3), 255, dtype=np.uint8)
        cv2.line(image, (20, 250), (480, 40), (0, 0, 255), 8)

        mask = build_pink_watermark_mask(image)

        self.assertLess(mask.mean() / 255.0, 0.005)


if __name__ == "__main__":
    unittest.main()
