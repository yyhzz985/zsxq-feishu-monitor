#!/usr/bin/env python3
"""Low-memory Pillow fallback for the official Smartisan Notes layout."""

import io
import os
import re

from PIL import Image, ImageDraw, ImageFont


CANVAS_WIDTH = 1200
BG_CREAM = (254, 252, 246)
FRAME_COLOR = (238, 235, 227)
HEADER_COLOR = (77, 61, 46)
TEXT_COLOR = (118, 99, 81)
QUOTE_COLOR = (192, 181, 167)
FOOTER_COLOR = (215, 206, 193)
SEPARATOR_COLOR = (227, 218, 203)

CONTENT_LEFT = 130
CONTENT_RIGHT = 1070
CONTENT_WIDTH = CONTENT_RIGHT - CONTENT_LEFT
CONTENT_TOP = 123
HEADER_FONT_SIZE = 54
BODY_FONT_SIZE = 52
FOOTER_FONT_SIZE = 23
HEADER_LINE_HEIGHT = 68
BODY_LINE_HEIGHT = 91
BLANK_LINE_HEIGHT = 37
IMAGE_MARGIN_TOP = 44
IMAGE_MARGIN_BOTTOM = 8
SEPARATOR_MARGIN = 44
FOOTER_MARGIN_TOP = 102
FOOTER_BOTTOM = 80

IMAGE_PATTERN = re.compile(r"^!\[[^\]]*\]\(([^)]+)\)$")
RESAMPLE_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")


def _font_candidates(weight):
    if weight == "bold":
        env_name = "NOTES_FONT_BOLD"
        filename = "OPPOSans-B.ttf"
    elif weight == "medium":
        env_name = "NOTES_FONT_MEDIUM"
        filename = "OPPOSans-M.ttf"
    else:
        env_name = "NOTES_FONT_REGULAR"
        filename = "OPPOSans-R.ttf"
    return [
        os.environ.get(env_name, ""),
        os.path.join("/opt/notes-renderer/fonts", filename),
        "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "msyh.ttf",
        "simhei.ttf",
    ]


def _load_font(size, weight="regular"):
    for path in _font_candidates(weight):
        if not path:
            continue
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _text_width(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _wrap_text(text, font, max_width, draw):
    if not text:
        return [""]
    lines = []
    current = ""
    for char in text:
        candidate = current + char
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _parse_blocks(markdown_text, image_paths):
    blocks = []
    header_seen = False
    for raw_line in markdown_text.split("\n"):
        line = raw_line.strip()
        if not header_seen and not line:
            continue
        if not header_seen:
            blocks.append(("header", line))
            header_seen = True
            continue
        if not line:
            blocks.append(("blank", ""))
            continue
        image_match = IMAGE_PATTERN.match(line)
        if image_match:
            marker_url = image_match.group(1)
            image_path = image_paths.get(marker_url)
            if not image_path:
                raise RuntimeError("fallback image mapping missing: %s" % marker_url)
            blocks.append(("image", image_path))
        elif line == "---" or line.startswith("--- "):
            blocks.append(("separator", ""))
        elif line.startswith("📌"):
            blocks.append(("reference", line[1:].strip()))
        elif line.startswith("📎"):
            blocks.append(("attachment", line[1:].strip()))
        else:
            blocks.append(("text", line))
    return blocks


def _image_size(filepath):
    with Image.open(filepath) as image:
        source_width, source_height = image.size
    if source_width <= 0 or source_height <= 0:
        raise RuntimeError("fallback image has invalid dimensions: %s" % filepath)
    render_width = min(source_width, CONTENT_WIDTH)
    render_height = max(1, int(round(source_height * (render_width / float(source_width)))))
    return render_width, render_height


def _layout_blocks(blocks, fonts):
    scratch = Image.new("RGB", (1, 1), BG_CREAM)
    draw = ImageDraw.Draw(scratch)
    layout = []
    total_height = 0
    for block_type, value in blocks:
        if block_type == "header":
            for line in _wrap_text(value, fonts["header"], CONTENT_WIDTH, draw):
                layout.append(("header", line, HEADER_LINE_HEIGHT))
                total_height += HEADER_LINE_HEIGHT
            total_height += 33
        elif block_type == "blank":
            layout.append(("blank", "", BLANK_LINE_HEIGHT))
            total_height += BLANK_LINE_HEIGHT
        elif block_type == "separator":
            layout.append(("separator", "", SEPARATOR_MARGIN))
            total_height += SEPARATOR_MARGIN
        elif block_type == "image":
            width, height = _image_size(value)
            block_height = IMAGE_MARGIN_TOP + height + IMAGE_MARGIN_BOTTOM
            layout.append(("image", (value, width, height), block_height))
            total_height += block_height
        else:
            font = fonts["body"]
            wrap_width = CONTENT_WIDTH - 44 if block_type in ("reference", "attachment") else CONTENT_WIDTH
            for line in _wrap_text(value, font, wrap_width, draw):
                layout.append((block_type, line, BODY_LINE_HEIGHT))
                total_height += BODY_LINE_HEIGHT
    scratch.close()
    return layout, total_height


def _draw_frame(draw, total_height):
    outer_bottom = max(52, total_height - 196)
    inner_bottom = max(66, total_height - 211)
    draw.rectangle((29, 51, CANVAS_WIDTH - 29, outer_bottom), outline=FRAME_COLOR, width=2)
    draw.rectangle((44, 65, CANVAS_WIDTH - 44, inner_bottom), outline=FRAME_COLOR, width=2)
    corner_size = 11
    for x, y in (
        (18, 47),
        (CANVAS_WIDTH - 29, 47),
        (18, total_height - 203),
        (CANVAS_WIDTH - 29, total_height - 203),
    ):
        draw.rectangle((x, y, x + corner_size, y + corner_size), outline=FRAME_COLOR, fill=BG_CREAM, width=2)


def _draw_reference_icon(draw, x, y):
    draw.ellipse((x + 7, y + 15, x + 25, y + 33), outline=QUOTE_COLOR, width=3)
    draw.line((x + 16, y + 32, x + 16, y + 51), fill=QUOTE_COLOR, width=3)
    draw.line((x + 11, y + 51, x + 21, y + 51), fill=QUOTE_COLOR, width=3)


def _draw_attachment_icon(draw, x, y):
    draw.arc((x + 3, y + 8, x + 31, y + 55), 65, 290, fill=TEXT_COLOR, width=3)
    draw.arc((x + 10, y + 14, x + 25, y + 47), 65, 285, fill=TEXT_COLOR, width=3)


def local_notes_export(markdown_text, footer_brand="击球区小能手的星球", image_paths=None):
    image_paths = image_paths or {}
    fonts = {
        "header": _load_font(HEADER_FONT_SIZE, "bold"),
        "body": _load_font(BODY_FONT_SIZE, "regular"),
        "footer": _load_font(FOOTER_FONT_SIZE, "medium"),
    }
    blocks = _parse_blocks(markdown_text, image_paths)
    layout, content_height = _layout_blocks(blocks, fonts)
    footer_y = CONTENT_TOP + content_height + FOOTER_MARGIN_TOP
    total_height = footer_y + FOOTER_FONT_SIZE + FOOTER_BOTTOM
    image = Image.new("RGB", (CANVAS_WIDTH, total_height), BG_CREAM)
    draw = ImageDraw.Draw(image)
    _draw_frame(draw, total_height)

    y = CONTENT_TOP
    for block_type, value, block_height in layout:
        if block_type == "header":
            draw.text((CONTENT_LEFT, y), value, font=fonts["header"], fill=HEADER_COLOR)
        elif block_type == "text":
            draw.text((CONTENT_LEFT, y), value, font=fonts["body"], fill=TEXT_COLOR)
        elif block_type == "reference":
            _draw_reference_icon(draw, CONTENT_LEFT, y)
            draw.text((CONTENT_LEFT + 44, y), value, font=fonts["body"], fill=QUOTE_COLOR)
        elif block_type == "attachment":
            _draw_attachment_icon(draw, CONTENT_LEFT, y)
            draw.text((CONTENT_LEFT + 44, y), value, font=fonts["body"], fill=TEXT_COLOR)
        elif block_type == "separator":
            line_y = y + block_height // 2
            draw.line((CONTENT_LEFT, line_y, CONTENT_RIGHT, line_y), fill=SEPARATOR_COLOR, width=2)
        elif block_type == "image":
            filepath, render_width, render_height = value
            image_y = y + IMAGE_MARGIN_TOP
            image_x = CONTENT_LEFT + (CONTENT_WIDTH - render_width) // 2
            with Image.open(filepath) as source:
                rendered = source.convert("RGB")
                if rendered.size != (render_width, render_height):
                    rendered = rendered.resize((render_width, render_height), RESAMPLE_LANCZOS)
                image.paste(rendered, (image_x, image_y))
                rendered.close()
        y += block_height

    icon_size = 29
    icon_x = CONTENT_LEFT + 8
    icon_y = footer_y - 3
    draw.ellipse((icon_x, icon_y, icon_x + icon_size, icon_y + icon_size), fill=FOOTER_COLOR)
    t_font = _load_font(18, "bold")
    draw.text((icon_x + 9, icon_y + 3), "T", font=t_font, fill=BG_CREAM)
    draw.text(
        (icon_x + icon_size + 14, footer_y),
        footer_brand,
        font=fonts["footer"],
        fill=FOOTER_COLOR,
    )

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    data = output.getvalue()
    output.close()
    image.close()
    return data
