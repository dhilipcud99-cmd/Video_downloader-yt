"""
Clipbay backend — FastAPI application.

Exposes a small REST API used by the frontend single-page app:

  POST /api/info              -> probe a URL with yt-dlp, return metadata + formats
  POST /api/process           -> queue a download + trim/crop/convert job
  GET  /api/status/{job_id}   -> poll job progress
  GET  /api/download/{job_id} -> download the finished file
  DELETE /api/jobs/{job_id}   -> cancel / clean up a job

The frontend (static/) is mounted at "/".
"""
from __future__ import annotations

import logging
import shutil
import threading
import uuid
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .jobs import JOB_STORE, JobStatus
from .pipeline import run_job
from .video_info import ProbeError, probe_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("clipbay")

BASE_DIR = Path(__file__).resolve().parent.parent
WORK_DIR = Path("/tmp/clipbay")
WORK_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Clipbay API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this to your deployed frontend origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class InfoRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def must_look_like_url(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("Please provide a valid http(s) URL.")
        return v


class CropBox(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class ProcessRequest(BaseModel):
    url: str
    start_time: float = Field(ge=0, description="Trim start, in seconds")
    end_time: float = Field(gt=0, description="Trim end, in seconds")
    crop: Optional[CropBox] = None
    container: str = Field(default="mp4", description="mp4 | webm | mkv | mp3 | m4a")
    quality: str = Field(default="best", description="best | 1080 | 720 | 480 | 360 | audio")
    audio_only: bool = False
    confirm_permission: bool = Field(
        default=False,
        description="User attestation that they hold the rights / permission to download this media.",
    )

    @field_validator("url")
    @classmethod
    def must_look_like_url(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("Please provide a valid http(s) URL.")
        return v

    @field_validator("end_time")
    @classmethod
    def end_after_start(cls, v: float, info):
        return v


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/info")
def get_info(payload: InfoRequest):
    try:
        return probe_url(payload.url)
    except ProbeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error probing URL")
        raise HTTPException(status_code=500, detail="Could not read that video URL.") from exc


@app.post("/api/process")
def process(payload: ProcessRequest):
    if not payload.confirm_permission:
        raise HTTPException(
            status_code=400,
            detail="Please confirm you have the right to download this video before continuing.",
        )
    if payload.end_time <= payload.start_time:
        raise HTTPException(status_code=422, detail="End time must be after start time.")
    if payload.end_time - payload.start_time > 60 * 60 * 3:
        raise HTTPException(status_code=422, detail="Clips are limited to 3 hours.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = WORK_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    JOB_STORE.create(job_id)

    thread = threading.Thread(
        target=run_job,
        args=(job_id, job_dir, payload.model_dump()),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
def status(job_id: str):
    job = JOB_STORE.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    return job.public_dict()


@app.get("/api/download/{job_id}")
def download(job_id: str, background_tasks: BackgroundTasks):
    job = JOB_STORE.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    if job.status != JobStatus.COMPLETED or not job.output_path:
        raise HTTPException(status_code=409, detail="This job isn't finished yet.")

    path = Path(job.output_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="This file has expired. Please re-run the job.")

    return FileResponse(
        path,
        filename=path.name,
        media_type="application/octet-stream",
    )


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    job = JOB_STORE.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    job_dir = WORK_DIR / job_id
    shutil.rmtree(job_dir, ignore_errors=True)
    JOB_STORE.delete(job_id)
    return {"deleted": True}


# --------------------------------------------------------------------------- #
# Static frontend (mounted last so it doesn't shadow /api routes)
# --------------------------------------------------------------------------- #
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
