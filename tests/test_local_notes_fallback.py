import io
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from local_notes_fallback import (  # noqa: E402
    BG_CREAM,
    CANVAS_WIDTH,
    CONTENT_LEFT,
    CONTENT_RIGHT,
    _load_font,
    _parse_blocks,
    local_notes_export,
)


class LocalNotesFallbackTests(unittest.TestCase):
    def test_loads_a_real_chinese_font_on_supported_hosts(self):
        font = _load_font(52)
        self.assertNotEqual(font.__class__.__name__, "ImageFont")

    def test_only_first_nonempty_line_is_the_date_header_and_blank_lines_remain(self):
        blocks = _parse_blocks("2026-06-11 10:00\n第二行正文\n\n第四行正文", {})

        self.assertEqual(blocks[0], ("header", "2026-06-11 10:00"))
        self.assertEqual(blocks[1], ("text", "第二行正文"))
        self.assertIn(("blank", ""), blocks)
        self.assertEqual(blocks[-1], ("text", "第四行正文"))

    def test_reference_and_attachment_blocks_do_not_depend_on_emoji_glyphs(self):
        blocks = _parse_blocks("2026-06-11\n📌 引用原文\n📎 示例附件.pdf", {})
        self.assertEqual(blocks[1], ("reference", "引用原文"))
        self.assertEqual(blocks[2], ("attachment", "示例附件.pdf"))

    def test_duplicate_image_markers_remain_in_their_original_positions(self):
        marker = "note-local://duplicate"
        blocks = _parse_blocks(
            "2026-06-11\n![a](%s)\n中间正文\n![b](%s)" % (marker, marker),
            {marker: "duplicate.png"},
        )

        self.assertEqual(blocks[1], ("image", "duplicate.png"))
        self.assertEqual(blocks[2], ("text", "中间正文"))
        self.assertEqual(blocks[3], ("image", "duplicate.png"))

    def test_embeds_local_image_instead_of_drawing_a_placeholder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "red.png"
            Image.new("RGB", (320, 180), (250, 0, 0)).save(str(image_path))
            marker = "note-local://image-0"

            png = local_notes_export(
                "2026-06-11 10:00\n\n正文\n\n![image](%s)" % marker,
                "击球区小能手的星球",
                image_paths={marker: str(image_path)},
            )

        rendered = Image.open(io.BytesIO(png)).convert("RGB")
        self.assertEqual(rendered.width, CANVAS_WIDTH)
        self.assertEqual(rendered.getpixel((0, 0)), BG_CREAM)
        red_pixels = 0
        min_x = CANVAS_WIDTH
        max_x = 0
        for y in range(rendered.height):
            for x in range(CONTENT_LEFT, CONTENT_RIGHT):
                red, green, blue = rendered.getpixel((x, y))
                if red > 240 and green < 10 and blue < 10:
                    red_pixels += 1
                    min_x = min(min_x, x)
                    max_x = max(max_x, x)
        self.assertGreater(red_pixels, 50000)
        self.assertLessEqual(max_x - min_x + 1, 320)

    def test_preserves_image_order_and_aspect_ratio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            red_path = Path(temp_dir) / "red.png"
            blue_path = Path(temp_dir) / "blue.png"
            Image.new("RGB", (2000, 1000), (250, 0, 0)).save(str(red_path))
            Image.new("RGB", (1000, 2000), (0, 0, 250)).save(str(blue_path))
            mapping = {
                "note-local://red": str(red_path),
                "note-local://blue": str(blue_path),
            }

            png = local_notes_export(
                "2026-06-11\n\n![r](note-local://red)\n\n![b](note-local://blue)",
                image_paths=mapping,
            )

        rendered = Image.open(io.BytesIO(png)).convert("RGB")
        red_y = []
        blue_y = []
        for y in range(rendered.height):
            pixel = rendered.getpixel((CANVAS_WIDTH // 2, y))
            if pixel[0] > 240 and pixel[1] < 10:
                red_y.append(y)
            if pixel[2] > 240 and pixel[0] < 10:
                blue_y.append(y)
        self.assertTrue(red_y)
        self.assertTrue(blue_y)
        self.assertLess(max(red_y), min(blue_y))
        self.assertAlmostEqual(len(red_y) / (CONTENT_RIGHT - CONTENT_LEFT), 0.5, delta=0.05)
        self.assertAlmostEqual(len(blue_y) / (CONTENT_RIGHT - CONTENT_LEFT), 2.0, delta=0.08)


if __name__ == "__main__":
    unittest.main()
