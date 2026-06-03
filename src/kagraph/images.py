from __future__ import annotations

from typing import Any

from kaggle_benchmarks.content_types.images import ImageBase64, ImageContent, ImageURL
from kaggle_benchmarks.content_types import images as _images


def image_from_base64(
    data: str | bytes,
    *,
    format: str = "jpeg",
    caption: str | None = None,
    extra_api_params: dict[str, Any] | None = None,
) -> ImageBase64:
    """Create a kbench image payload from a base64 string."""

    return _images.from_base64(
        data,
        format=format,
        caption=caption,
        extra_api_params=extra_api_params,
    )


def image_from_path(path: str, *, extra_api_params: dict[str, Any] | None = None) -> ImageBase64:
    """Create a kbench image payload from a local image path."""

    return _images.from_path(path, extra_api_params=extra_api_params)


def image_from_url(
    url: str,
    *,
    caption: str | None = None,
    extra_api_params: dict[str, Any] | None = None,
) -> ImageURL:
    """Create a kbench image payload from an image URL."""

    return _images.from_url(
        url,
        caption=caption,
        extra_api_params=extra_api_params,
    )


__all__ = [
    "ImageBase64",
    "ImageContent",
    "ImageURL",
    "image_from_base64",
    "image_from_path",
    "image_from_url",
]
