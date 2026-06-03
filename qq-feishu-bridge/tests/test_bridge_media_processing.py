import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

import cv2
import numpy as np


def _load_bridge(temp_dir):
    os.environ["QQ_BRIDGE_DB"] = os.path.join(temp_dir, "bridge.db")
    os.environ["QQ_BRIDGE_TEMP"] = temp_dir
    sys.modules.pop("qq_feishu_bridge", None)
    return importlib.import_module("qq_feishu_bridge")


class BridgeMediaProcessingTest(unittest.TestCase):
    def test_watermark_clean_failure_marks_failed_and_alerts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = _load_bridge(temp_dir)
            bridge._config = {
                "groups": [
                    {
                        "group_id": 123,
                        "media_processing": {"remove_watermark": True},
                    }
                ],
                "media_processing": {"remove_watermark": False},
            }
            conn = None
            try:
                conn = bridge.init_db()
                input_path = os.path.join(temp_dir, "plain.png")
                cv2.imwrite(input_path, np.full((120, 160, 3), 255, dtype=np.uint8))

                with mock.patch.object(bridge, "send_alert") as send_alert:
                    result_path = bridge.prepare_image_for_upload(input_path, 123, 456, conn)

                row = conn.execute(
                    "SELECT status,last_error FROM qq_messages WHERE message_id=?",
                    ("456",),
                ).fetchone()
                self.assertIsNone(result_path)
                self.assertEqual(row["status"], "failed")
                self.assertIn("watermark clean failed", row["last_error"])
                send_alert.assert_called_once()
            finally:
                if conn:
                    conn.close()


if __name__ == "__main__":
    unittest.main()
