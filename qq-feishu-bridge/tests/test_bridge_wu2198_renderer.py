import asyncio
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = REPO_ROOT / "qq-feishu-bridge" / "bridge_wu2198.py"
sys.path.insert(0, str(REPO_ROOT / "src"))


def load_bridge(temp_dir):
    os.environ["TEMP_DIR"] = str(Path(temp_dir) / "temp")
    os.environ["ARCHIVE_DIR"] = str(Path(temp_dir) / "archive")
    spec = importlib.util.spec_from_file_location("bridge_wu2198_under_test", BRIDGE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Wu2198RendererTests(unittest.TestCase):
    def test_message_images_keep_segment_order_and_live_until_render_finishes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = load_bridge(temp_dir)
            source_buffer = io.BytesIO()
            Image.new("RGB", (80, 40), (250, 0, 0)).save(source_buffer, format="PNG")
            note_buffer = io.BytesIO()
            Image.new("RGB", (1200, 400), (254, 252, 246)).save(note_buffer, format="PNG")
            captured = {}

            bridge.download_qq_url = lambda _url: source_buffer.getvalue()
            bridge.add_watermark = lambda data: data
            bridge.fs_send_image_to_chats = lambda path: captured.setdefault("sent", path) or True

            def fake_render(request, logger=None, on_fallback=None):
                captured["request"] = request
                captured["exists_during_render"] = all(
                    os.path.exists(item.local_path) for item in request.images
                )
                return note_buffer.getvalue()

            bridge.render_note = fake_render
            event = {
                "time": 1781143200,
                "message": [
                    {"type": "text", "data": {"text": "第一段"}},
                    {"type": "image", "data": {"url": "https://example.com/image.png"}},
                    {"type": "text", "data": {"text": "第二段"}},
                ],
            }

            asyncio.run(bridge.process_message(event))

            request = captured["request"]
            marker = request.images[0].marker_url
            self.assertTrue(captured["exists_during_render"])
            self.assertLess(request.markdown.index("第一段"), request.markdown.index(marker))
            self.assertLess(request.markdown.index(marker), request.markdown.index("第二段"))
            self.assertFalse(os.path.exists(request.images[0].local_path))
            self.assertTrue(os.path.exists(captured["sent"]))

    def test_skipped_message_cleans_images_downloaded_before_a_url_segment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = load_bridge(temp_dir)
            source_buffer = io.BytesIO()
            Image.new("RGB", (80, 40), (250, 0, 0)).save(source_buffer, format="PNG")
            bridge.download_qq_url = lambda _url: source_buffer.getvalue()
            bridge.render_note = lambda *_args, **_kwargs: self.fail("skipped message must not render")
            event = {
                "time": 1781143200,
                "message": [
                    {"type": "image", "data": {"url": "https://example.com/image.png"}},
                    {"type": "text", "data": {"text": "https://example.com/skip"}},
                ],
            }

            asyncio.run(bridge.process_message(event))

            self.assertEqual(list(Path(bridge.TEMP_DIR).glob("note_img_*.png")), [])


if __name__ == "__main__":
    unittest.main()
