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


class BridgeTextFilterTest(unittest.TestCase):
    def test_text_with_url_is_forwarded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = _load_bridge(temp_dir)
            conn = bridge.init_db()
            event = {
                "group_id": 955876053,
                "message_id": 1001,
                "message": [{"type": "text", "data": {"text": "https://example.com/a"}}],
            }
            try:
                with mock.patch.object(bridge, "fs_send_text") as send_text:
                    asyncio.run(bridge.process_message(None, event, conn))

                send_text.assert_called_once_with("https://example.com/a", 955876053)
            finally:
                conn.close()

    def test_forward_message_is_skipped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = _load_bridge(temp_dir)
            conn = bridge.init_db()
            event = {
                "group_id": 955876053,
                "message_id": 1002,
                "message": [{"type": "forward", "data": {}}],
            }
            try:
                with mock.patch.object(bridge, "fs_send_text") as send_text:
                    asyncio.run(bridge.process_message(None, event, conn))

                send_text.assert_not_called()
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
