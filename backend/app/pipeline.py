"""Runs one job end to end: download (yt-dlp) -> trim/crop/convert (ffmpeg).

Progress is mapped onto a single 0-100 scale for the frontend:
  0   - 10   queued / resolving formats
  10  - 55   downloading source media
  55  - 100  ffmpeg trim / crop / encode
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

import yt_dlp

from .jobs import JOB_STORE, JobStatus

logger = logging.getLogger("clipbay.pipeline")

# Container -> (video codec, audio codec, extra ffmpeg args)
CONTAINER_CODECS = {
    "mp4": {"vcodec": "libx264", "acodec": "aac", "extra": ["-movflags", "+faststart"]},
    "webm": {"vcodec": "libvpx-vp9", "acodec": "libopus", "extra": []},
    "mkv": {"vcodec": "libx264", "acodec": "aac", "extra": []},
    "mp3": {"vcodec": None, "acodec": "libmp3lame", "extra": []},
    "m4a": {"vcodec": None, "acodec": "aac", "extra": []},
}

TIME_RE = re.compile(r"out_time_ms=(\d+)")
TIME_RE_ALT = re.compile(r"out_time=(\d{2}):(\d{2}):(\d{2})\.(\d+)")


def _format_selector(quality: str, audio_only: bool) -> str:
    if audio_only or quality == "audio":
        return "bestaudio/best"
    if quality == "best":
        return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
    height = "".join(ch for ch in quality if ch.isdigit()) or "1080"
    return (
        f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]"
        f"/bestvideo[height<={height}]+bestaudio/best[height<={height}]"
    )


def _download_source(job_id: str, url: str, job_dir: Path, quality: str, audio_only: bool) -> Path:
    outtmpl = str(job_dir / "source.%(ext)s")

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes") or 0
            if total:
                pct = downloaded / total
            else:
                pct = 0.0
            mapped = 10 + pct * 45  # 10-55%
            speed = d.get("_speed_str", "").strip()
            JOB_STORE.update(
                job_id,
                status=JobStatus.DOWNLOADING,
                progress=mapped,
                message=f"Downloading source ({speed})" if speed else "Downloading source…",
            )
        elif d.get("status") == "finished":
            JOB_STORE.update(job_id, progress=55, message="Download complete, starting processing…")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "outtmpl": outtmpl,
        "format": _format_selector(quality, audio_only),
        "merge_output_format": "mp4" if not audio_only else None,
        "progress_hooks": [hook],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)

    resolved = Path(filepath)
    if not resolved.exists():
        # merge_output_format can change the extension after prepare_filename
        candidates = list(job_dir.glob("source.*"))
        if not candidates:
            raise RuntimeError("yt-dlp reported success but no output file was found.")
        resolved = candidates[0]
    return resolved


def _run_ffmpeg_with_progress(job_id: str, cmd: list[str], clip_duration: float) -> None:
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1,
    )

    assert process.stdout is not None
    for line in process.stdout:
        seconds: Optional[float] = None
        m = TIME_RE.search(line)
        if m:
            seconds = int(m.group(1)) / 1_000_000
        else:
            m2 = TIME_RE_ALT.search(line)
            if m2:
                h, mnt, s, _frac = m2.groups()
                seconds = int(h) * 3600 + int(mnt) * 60 + int(s)

        if seconds is not None and clip_duration > 0:
            pct = min(seconds / clip_duration, 1.0)
            mapped = 55 + pct * 45  # 55-100%
            JOB_STORE.update(
                job_id,
                status=JobStatus.PROCESSING,
                progress=mapped,
                message="Encoding your clip…",
            )

    returncode = process.wait()
    if returncode != 0:
        raise RuntimeError("ffmpeg failed while processing the clip. See server logs for details.")


def _build_ffmpeg_cmd(
    source: Path,
    dest: Path,
    start: float,
    end: float,
    crop: Optional[dict],
    container: str,
    audio_only: bool,
) -> list[str]:
    codecs = CONTAINER_CODECS.get(container, CONTAINER_CODECS["mp4"])
    duration = max(end - start, 0.01)

    cmd = [
        "ffmpeg",
        "-y",
        "-ss", f"{start:.3f}",
        "-i", str(source),
        "-t", f"{duration:.3f}",
    ]

    filters = []
    if crop and not audio_only:
        filters.append(f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']}")
    if filters:
        cmd += ["-vf", ",".join(filters)]

    if audio_only or codecs["vcodec"] is None:
        cmd += ["-vn", "-acodec", codecs["acodec"]]
    else:
        cmd += ["-c:v", codecs["vcodec"], "-c:a", codecs["acodec"]]
        cmd += codecs["extra"]

    cmd += ["-progress", "pipe:1", "-nostats", str(dest)]
    return cmd


def run_job(job_id: str, job_dir: Path, params: dict) -> None:
    try:
        JOB_STORE.update(job_id, status=JobStatus.FETCHING_INFO, progress=2, message="Resolving formats…")

        source_path = _download_source(
            job_id,
            params["url"],
            job_dir,
            params["quality"],
            params["audio_only"],
        )

        container = params["container"]
        audio_only = params["audio_only"] or container in ("mp3", "m4a")
        dest_path = job_dir / f"clip.{container}"

        cmd = _build_ffmpeg_cmd(
            source_path,
            dest_path,
            params["start_time"],
            params["end_time"],
            params.get("crop"),
            container,
            audio_only,
        )

        clip_duration = params["end_time"] - params["start_time"]
        JOB_STORE.update(job_id, status=JobStatus.PROCESSING, progress=55, message="Trimming and encoding…")
        _run_ffmpeg_with_progress(job_id, cmd, clip_duration)

        if not dest_path.exists() or dest_path.stat().st_size == 0:
            raise RuntimeError("ffmpeg finished but produced no output file.")

        JOB_STORE.update(
            job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            message="Done",
            output_path=str(dest_path),
            output_name=dest_path.name,
            output_size_bytes=dest_path.stat().st_size,
        )

        # Free disk space: we no longer need the raw source once the clip is cut.
        try:
            if source_path.exists() and source_path != dest_path:
                source_path.unlink()
        except OSError:
            pass

    except Exception as exc:  # noqa: BLE001
        logger.exception("Job %s failed", job_id)
        JOB_STORE.update(
            job_id,
            status=JobStatus.FAILED,
            message="Job failed",
            error=str(exc),
        )
