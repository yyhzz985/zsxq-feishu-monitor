import os
import tempfile
import unittest

import cv2
import numpy as np

from services.media_processing_service import process_downloaded_image
from utils.watermark_cleaner import build_pink_watermark_mask


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_DIR = os.path.join(ROOT_DIR, "tests", "fixtures")
SAMPLE_NAME = "\u6d4b\u8bd5\u53bb\u6c34\u53703.png"


def _read_image(path):
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise AssertionError("failed to read image: %s" % path)
    return image


class MediaProcessingServiceTest(unittest.TestCase):
    def test_disabled_config_returns_original_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = os.path.join(temp_dir, "plain.png")
            cv2.imwrite(input_path, np.full((80, 120, 3), 255, dtype=np.uint8))

            result_path = process_downloaded_image(input_path, temp_dir, {"remove_watermark": False})

            self.assertEqual(result_path, input_path)

    def test_enabled_config_returns_cleaned_copy(self):
        input_path = os.path.join(FIXTURE_DIR, SAMPLE_NAME)
        with tempfile.TemporaryDirectory() as temp_dir:
            result_path = process_downloaded_image(input_path, temp_dir, {"remove_watermark": True})

            original_mask = build_pink_watermark_mask(_read_image(input_path))
            cleaned_mask = build_pink_watermark_mask(_read_image(result_path))
            self.assertNotEqual(result_path, input_path)
            self.assertTrue(os.path.exists(result_path))
            self.assertLess(cleaned_mask.mean(), original_mask.mean() * 0.45)


if __name__ == "__main__":
    unittest.main()
