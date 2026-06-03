import os

from utils.watermark_cleaner import remove_pink_watermark


def is_watermark_removal_enabled(config):
    return bool((config or {}).get("remove_watermark", False))


def process_downloaded_image(image_path, temp_dir, config):
    if not is_watermark_removal_enabled(config):
        return image_path

    base = os.path.splitext(os.path.basename(image_path))[0]
    output_path = os.path.join(temp_dir, base + "_cleaned.png")
    return remove_pink_watermark(image_path, output_path)
