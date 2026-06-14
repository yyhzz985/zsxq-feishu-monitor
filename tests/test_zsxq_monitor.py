import importlib.util
import io
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path

from PIL import Image


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "src" / "zsxq_monitor.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))


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


class TopicUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.saved_env = {}
        self.keys = [
            "ZSXQ_ENV_FILE",
            "ZSXQ_LOG_DIR",
            "ZSXQ_TEMP_DIR",
            "ZSXQ_SAVE_DIR",
        ]
        for key in self.keys:
            self.saved_env[key] = os.environ.get(key)

        os.environ["ZSXQ_ENV_FILE"] = str(base / ".env")
        os.environ["ZSXQ_LOG_DIR"] = str(base / "logs")
        os.environ["ZSXQ_TEMP_DIR"] = str(base / "temp")
        os.environ["ZSXQ_SAVE_DIR"] = str(base / "notes")
        self.monitor = load_monitor_module()
        self.db_path = str(base / "state.db")
        self.conn = self.monitor.init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp_dir.cleanup()

    def make_topic(self, images=None, topic_id=123, create_time=None):
        return {
            "topic_id": topic_id,
            "create_time": create_time or time.strftime("%Y-%m-%dT%H:%M:%S.000+0800"),
            "type": "talk",
            "talk": {
                "text": "先发文字，后补图片",
                "images": images or [],
                "files": [],
            },
        }

    def topic_row(self, topic_id=123):
        return self.conn.execute(
            "select * from topics where topic_id = ?",
            (str(topic_id),),
        ).fetchone()

    def test_sent_topic_becomes_updated_when_image_is_added(self):
        self.monitor.upsert_topic(self.conn, self.make_topic())
        self.monitor.mark_topic_sent(self.conn, 123, "old-note.png", "om_old")

        self.monitor.upsert_topic(
            self.conn,
            self.make_topic(images=[{"image_id": "img_new"}]),
        )

        row = self.topic_row()
        self.assertEqual(row["status"], "updated")
        self.assertIn("img_new", row["topic_json"])
        self.assertEqual(self.monitor.get_pending_topics(self.conn)[0]["topic_id"], 123)

    def test_seen_old_unknown_topic_is_not_inserted_for_update_check(self):
        self.monitor.upsert_topic(self.conn, self.make_topic(topic_id=999), allow_insert=False)

        self.assertIsNone(self.topic_row(topic_id=999))

    def test_updated_topic_rerenders_even_when_old_archive_exists(self):
        old_path = Path(self.temp_dir.name) / "old.png"
        new_path = Path(self.temp_dir.name) / "new.png"
        old_path.write_bytes(b"old")
        new_path.write_bytes(b"new")

        self.monitor.upsert_topic(self.conn, self.make_topic())
        self.monitor.mark_topic_sent(self.conn, 123, str(old_path), "om_old")
        self.monitor.upsert_topic(
            self.conn,
            self.make_topic(images=[{"image_id": "img_new"}]),
        )

        calls = []
        original_render = self.monitor.render_topic_note
        original_send = self.monitor.fs_send_image
        original_log = self.monitor.log_msg
        try:
            def fake_render(topic, ztok, group_name=None):
                _text, images, files = self.monitor.extract_topic_content(topic)
                calls.append([img.get("image_id") for img in images])
                return str(new_path), files

            def fake_send(filepath, idempotency_key=None):
                calls.append(filepath)
                return {"ok": True, "message_id": "om_new", "error": ""}

            self.monitor.render_topic_note = fake_render
            self.monitor.fs_send_image = fake_send
            self.monitor.log_msg = lambda _message: None

            record = self.monitor.get_pending_topics(self.conn)[0]
            sent = self.monitor.process_topic_record(self.conn, record, "token")
        finally:
            self.monitor.render_topic_note = original_render
            self.monitor.fs_send_image = original_send
            self.monitor.log_msg = original_log

        self.assertTrue(sent)
        self.assertEqual(calls[0], ["img_new"])
        self.assertEqual(calls[1], str(new_path))
        row = self.topic_row()
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["archive_path"], str(new_path))

    def test_stale_sent_topic_change_does_not_resend_historical_content(self):
        self.monitor.upsert_topic(
            self.conn,
            self.make_topic(create_time="2020-01-01T10:00:00.000+0800"),
        )
        self.monitor.mark_topic_sent(self.conn, 123, "old-note.png", "om_old")

        self.monitor.upsert_topic(
            self.conn,
            self.make_topic(
                images=[{"image_id": "old_img"}],
                create_time="2020-01-01T10:00:00.000+0800",
            ),
        )

        row = self.topic_row()
        self.assertEqual(row["status"], "sent")
        self.assertIn("old_img", row["topic_json"])
        self.assertEqual(self.monitor.get_pending_topics(self.conn), [])

    def test_render_keeps_downloaded_image_until_unified_renderer_finishes(self):
        image_buffer = io.BytesIO()
        Image.new("RGB", (80, 40), (250, 0, 0)).save(image_buffer, format="PNG")
        note_buffer = io.BytesIO()
        Image.new("RGB", (1200, 400), (254, 252, 246)).save(note_buffer, format="PNG")
        captured = {}

        original_url = self.monitor.zsxq_image_url
        original_get = self.monitor.http_get
        original_render = self.monitor.render_note
        original_watermark = self.monitor.add_watermark
        try:
            self.monitor.zsxq_image_url = lambda _image_id, _token: "https://example.com/image.png"
            self.monitor.http_get = lambda _url: image_buffer.getvalue()

            def fake_render(request, logger=None, on_fallback=None):
                captured["request"] = request
                captured["exists_during_render"] = all(
                    os.path.exists(item.local_path) for item in request.images
                )
                return note_buffer.getvalue()

            self.monitor.render_note = fake_render
            self.monitor.add_watermark = lambda data: data

            save_path, _files = self.monitor.render_topic_note(
                self.make_topic(images=[{"image_id": "img_new"}]),
                "token",
                group_name="击球区小能手的星球",
            )
        finally:
            self.monitor.zsxq_image_url = original_url
            self.monitor.http_get = original_get
            self.monitor.render_note = original_render
            self.monitor.add_watermark = original_watermark

        request = captured["request"]
        self.assertTrue(captured["exists_during_render"])
        self.assertEqual(request.source, "zsxq")
        self.assertEqual(len(request.images), 1)
        self.assertIn(request.images[0].marker_url, request.markdown)
        self.assertFalse(os.path.exists(request.images[0].local_path))
        self.assertTrue(os.path.exists(save_path))

    def test_attachment_filename_is_limited_by_utf8_bytes(self):
        long_name = (
            "GS-生益科技（600183.SS）— AI用覆铜板（CCL）产能扩张叠加产品结构向高端升级；"
            "目标价上调至人民币217.6元；维持买入 "
            "Shengyi Tech (600183.SS) AI CCL capacity expansion with mix upgrade to high-end products; "
            "TP raised to Rmb217.6; maintain buy.pdf"
        )

        safe_name = self.monitor.safe_filename(long_name)
        archive_path = self.monitor.unique_path(
            self.temp_dir.name,
            f"45544551455244828_{safe_name}",
        )

        self.assertLessEqual(len(safe_name.encode("utf-8")), 180)
        self.assertLessEqual(len(os.path.basename(archive_path).encode("utf-8")), 180)
        self.assertTrue(os.path.basename(archive_path).endswith(".pdf"))


if __name__ == "__main__":
    unittest.main()
