"""Probe a URL with yt-dlp and return a compact, frontend-friendly summary."""
from __future__ import annotations

from typing import Any, Dict, List

import yt_dlp


class ProbeError(Exception):
    """Raised when a URL can't be read (unsupported site, private video, bad URL, ...)."""


def _base_ydl_opts() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        # Keep probing fast: don't resolve every format's filesize etc.
        "extract_flat": False,
    }


def _distinct_resolutions(formats: List[dict]) -> List[Dict[str, Any]]:
    """Collapse yt-dlp's raw format list into a short list of pickable qualities."""
    seen_heights = set()
    out: List[Dict[str, Any]] = []
    for f in sorted(formats, key=lambda f: (f.get("height") or 0), reverse=True):
        height = f.get("height")
        vcodec = f.get("vcodec")
        if not height or vcodec == "none":
            continue
        if height in seen_heights:
            continue
        seen_heights.add(height)
        out.append(
            {
                "label": f"{height}p",
                "value": str(height),
                "fps": f.get("fps"),
                "ext": f.get("ext"),
            }
        )
    return out


def probe_url(url: str) -> Dict[str, Any]:
    try:
        with yt_dlp.YoutubeDL(_base_ydl_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise ProbeError(
            "Couldn't read that URL. Double-check the link, and that the video is "
            "public (or that you're allowed to access it)."
        ) from exc

    if info is None:
        raise ProbeError("No video information was returned for that URL.")

    formats = info.get("formats") or []
    duration = info.get("duration") or 0

    return {
        "title": info.get("title") or "Untitled video",
        "duration": duration,
        "thumbnail": info.get("thumbnail"),
        "uploader": info.get("uploader"),
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "webpage_url": info.get("webpage_url") or url,
        "width": info.get("width"),
        "height": info.get("height"),
        "resolutions": _distinct_resolutions(formats),
        "has_audio_only": any(
            (f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")) for f in formats
        ),
    }
