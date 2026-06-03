import os

import cv2
import numpy as np


class WatermarkCleanError(Exception):
    pass


MIN_MASK_RATIO = 0.004
MAX_MASK_RATIO = 0.08


def _read_image(path):
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise WatermarkCleanError("image decode failed")
    return image


def _write_image(path, image):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    ok, data = cv2.imencode(".png", image)
    if not ok:
        raise WatermarkCleanError("image encode failed")
    data.tofile(path)


def build_pink_watermark_mask(image):
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        raise WatermarkCleanError("invalid image")

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    b, g, r = cv2.split(image)

    r16 = r.astype(np.int16)
    g16 = g.astype(np.int16)
    b16 = b.astype(np.int16)
    red_hue = (h <= 12) | (h >= 168)
    pink = (
        red_hue
        & (s >= 15)
        & (s <= 135)
        & (v >= 85)
        & ((r16 - g16) >= 12)
        & ((r16 - b16) >= 12)
    )

    mask = pink.astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    return mask


def remove_pink_watermark(input_path, output_path):
    image = _read_image(input_path)
    mask = build_pink_watermark_mask(image)
    ratio = float(np.count_nonzero(mask)) / float(mask.size)
    if ratio < MIN_MASK_RATIO:
        raise WatermarkCleanError("pink watermark not detected")
    if ratio > MAX_MASK_RATIO:
        raise WatermarkCleanError("pink mask too large")

    cleaned = cv2.inpaint(image, mask, 3, cv2.INPAINT_TELEA)
    _write_image(output_path, cleaned)
    return output_path
