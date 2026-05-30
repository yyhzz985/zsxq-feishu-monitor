import importlib.util
import io
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "src" / "zsxq_monitor.py"


def load_monitor_module():
    spec = importlib.util.spec_from_file_location("zsxq_monitor_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FeishuMultiChatTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.saved_env = {}
        self.keys = [
            "FEISHU_SEND_MODE",
            "FEISHU_CHAT_ID",
            "FEISHU_CONTENT_CHAT_IDS",
            "FEISHU_ALERT_CHAT_ID",
            "FEISHU_ALERT_CHAT_IDS",
            "ZSXQ_ENV_FILE",
            "ZSXQ_LOG_DIR",
            "ZSXQ_TEMP_DIR",
        ]
        for key in self.keys:
            self.saved_env[key] = os.environ.get(key)

        os.environ["ZSXQ_ENV_FILE"] = str(base / ".env")
        os.environ["ZSXQ_LOG_DIR"] = str(base / "logs")
        os.environ["ZSXQ_TEMP_DIR"] = str(base / "temp")
        os.environ["FEISHU_SEND_MODE"] = "openapi"
        os.environ["FEISHU_CHAT_ID"] = "oc_test"
        os.environ["FEISHU_CONTENT_CHAT_IDS"] = "oc_formal"
        os.environ["FEISHU_ALERT_CHAT_ID"] = "oc_test"
        self.monitor = load_monitor_module()

    def tearDown(self):
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp_dir.cleanup()

    def test_image_send_fails_when_any_target_chat_fails(self):
        calls = []
        original_send = self.monitor.feishu_send_image_openapi
        original_log = self.monitor.log_msg
        try:
            def fake_send(filepath, idempotency_key=None, chat_id=None):
                calls.append((filepath, idempotency_key, chat_id))
                if chat_id == "oc_test":
                    return {"ok": True, "message_id": "om_test", "error": ""}
                return {"ok": False, "message_id": "", "error": "HTTP 400 formal group"}

            self.monitor.feishu_send_image_openapi = fake_send
            self.monitor.log_msg = lambda _message: None

            result = self.monitor.fs_send_image("note.png", "idem-topic")
        finally:
            self.monitor.feishu_send_image_openapi = original_send
            self.monitor.log_msg = original_log

        self.assertFalse(result["ok"])
        self.assertIn("oc_formal", result["error"])
        self.assertEqual([call[2] for call in calls], ["oc_test", "oc_formal"])
        self.assertEqual(calls[0][1], "idem-topic_oc_test")
        self.assertEqual(calls[1][1], "idem-topic_oc_formal")

    def test_text_alert_does_not_send_to_content_only_chat(self):
        calls = []
        original_send = self.monitor.feishu_send_text_openapi
        original_log = self.monitor.log_msg
        try:
            def fake_send(text, idempotency_key=None, chat_id=None):
                calls.append((text, idempotency_key, chat_id))
                return {"ok": True, "message_id": "om_text", "error": ""}

            self.monitor.feishu_send_text_openapi = fake_send
            self.monitor.log_msg = lambda _message: None

            result = self.monitor.fs_send_text("system alert", "idem-alert")
        finally:
            self.monitor.feishu_send_text_openapi = original_send
            self.monitor.log_msg = original_log

        self.assertTrue(result["ok"])
        self.assertEqual([call[2] for call in calls], ["oc_test"])

    def test_http_post_returns_feishu_error_body_for_http_error(self):
        original_urlopen = self.monitor.urllib.request.urlopen
        try:
            def fake_urlopen(req, timeout=None, context=None):
                raise urllib.error.HTTPError(
                    req.full_url,
                    400,
                    "Bad Request",
                    {},
                    io.BytesIO(b'{"code":230001,"msg":"bot is not in chat"}'),
                )

            self.monitor.urllib.request.urlopen = fake_urlopen

            body, status = self.monitor.http_post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                b"{}",
                {},
                timeout=1,
            )
        finally:
            self.monitor.urllib.request.urlopen = original_urlopen

        self.assertEqual(status, 400)
        self.assertIn(b"bot is not in chat", body)


if __name__ == "__main__":
    unittest.main()
