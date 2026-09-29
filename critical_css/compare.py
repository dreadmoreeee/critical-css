"""Pixel comparison of two screenshots with Pillow."""

from __future__ import annotations

import io

from PIL import Image, ImageChops

__all__ = ["compare_images"]


def _load(data) -> Image.Image:
    if isinstance(data, Image.Image):
        return data.convert("RGB")
    return Image.open(io.BytesIO(data)).convert("RGB")


def compare_images(a, b, threshold: int = 16, diff_path: str | None = None) -> dict:
    """Compare two images.

    A pixel differs when any RGB channel differs by more than ``threshold``
    (0-255). Returns ``{"pixels", "differing", "percent"}``. If
    ``diff_path`` is given, writes a PNG: the first image dimmed to grey with
    differing pixels in red.
    """
    img_a, img_b = _load(a), _load(b)
    if img_a.size != img_b.size:
        w = max(img_a.width, img_b.width)
        h = max(img_a.height, img_b.height)
        pad_a = Image.new("RGB", (w, h), (255, 0, 255))
        pad_b = Image.new("RGB", (w, h), (0, 255, 0))
        pad_a.paste(img_a, (0, 0))
        pad_b.paste(img_b, (0, 0))
        img_a, img_b = pad_a, pad_b
    r, g, bl = ImageChops.difference(img_a, img_b).split()
    worst = ImageChops.lighter(ImageChops.lighter(r, g), bl)
    mask = worst.point(lambda v: 255 if v > threshold else 0)
    differing = mask.histogram()[255]
    pixels = img_a.width * img_a.height
    if diff_path:
        base = img_a.convert("L").point(lambda v: 128 + v // 2).convert("RGB")
        base.paste(Image.new("RGB", img_a.size, (230, 0, 0)), (0, 0), mask)
        base.save(diff_path)
    return {
        "pixels": pixels,
        "differing": differing,
        "percent": round(100.0 * differing / pixels, 3) if pixels else 0.0,
    }
