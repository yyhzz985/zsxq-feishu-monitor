import asyncio
import importlib
import os
import sys
import tempfile
import unittest
from unittest import mock


def _load_bridge(temp_dir):
    os.environ["QQ_BRIDGE_DB"] = os.path.join(temp_dir, "bridge.db")
    os.environ["QQ_BRIDGE_TEMP"] = temp_dir
    sys.modules.pop("qq_feishu_bridge", None)
    return importlib.import_module("qq_feishu_bridge")


class BridgeDeliveryStatusTest(unittest.TestCase):
    def test_text_send_failure_marks_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = _load_bridge(temp_dir)
            conn = bridge.init_db()
            event = {
                "group_id": 955876053,
                "message_id": 2001,
                "message": "hello",
            }
            try:
                with mock.patch.object(bridge, "fs_send_text", return_value=False), \
                     mock.patch.object(bridge, "send_alert"):
                    asyncio.run(bridge.process_message(None, event, conn))

                row = conn.execute(
                    "SELECT status,last_error FROM qq_messages WHERE message_id=?",
                    ("2001",),
                ).fetchone()
                self.assertEqual(row["status"], "failed")
                self.assertIn("text send failed", row["last_error"])
            finally:
                conn.close()

    def test_image_send_failure_marks_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = _load_bridge(temp_dir)
            conn = bridge.init_db()
            input_path = os.path.join(temp_dir, "input.png")
            with open(input_path, "wb") as f:
                f.write(b"image-data")
            event = {
                "group_id": 955876053,
                "message_id": 2002,
                "message": [
                    {"type": "image", "data": {"url": "https://example.com/a.png"}},
                ],
            }
            try:
                with mock.patch.object(bridge, "download_qq_url", return_value=b"image-data"), \
                     mock.patch.object(bridge, "prepare_image_for_upload", return_value=input_path), \
                     mock.patch.object(bridge, "fs_send_media", return_value=False), \
                     mock.patch.object(bridge, "send_alert"):
                    asyncio.run(bridge.process_message(None, event, conn))

                row = conn.execute(
                    "SELECT status,last_error FROM qq_messages WHERE message_id=?",
                    ("2002",),
                ).fetchone()
                self.assertEqual(row["status"], "failed")
                self.assertIn("image send failed", row["last_error"])
            finally:
                conn.close()

    def test_failed_message_is_not_skipped_before_max_retries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = _load_bridge(temp_dir)
            bridge._config = {"retry": {"max_retries": 3}}
            conn = bridge.init_db()
            try:
                bridge.insert_msg(conn, 2003, 955876053, "", "text", "hello")
                bridge.mark_failed(conn, 2003, "temporary failure")

                self.assertFalse(bridge.should_skip_message(conn, 2003))

                bridge.mark_failed(conn, 2003, "temporary failure")
                bridge.mark_failed(conn, 2003, "temporary failure")
                self.assertTrue(bridge.should_skip_message(conn, 2003))
            finally:
                conn.close()

    def test_received_message_is_not_skipped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = _load_bridge(temp_dir)
            conn = bridge.init_db()
            try:
                bridge.insert_msg(conn, 2004, 955876053, "", "text", "hello")

                self.assertFalse(bridge.should_skip_message(conn, 2004))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
