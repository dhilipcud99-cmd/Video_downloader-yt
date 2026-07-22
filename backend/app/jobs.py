"""Thread-safe in-memory job registry.

For a single-process deployment (Render/Railway free/hobby tiers run one
worker) an in-memory dict is enough. If you scale to multiple workers,
swap this for Redis (the interface below is intentionally tiny so that's
a drop-in change — see README "Scaling beyond one worker").
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


class JobStatus(str, Enum):
    QUEUED = "queued"
    FETCHING_INFO = "fetching_info"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0  # 0-100
    message: str = "Queued"
    error: Optional[str] = None
    output_path: Optional[str] = None
    output_name: Optional[str] = None
    output_size_bytes: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def public_dict(self) -> dict:
        return {
            "job_id": self.id,
            "status": self.status.value,
            "progress": round(self.progress, 1),
            "message": self.message,
            "error": self.error,
            "output_name": self.output_name,
            "output_size_bytes": self.output_size_bytes,
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, job_id: str) -> Job:
        with self._lock:
            job = Job(id=job_id)
            self._jobs[job_id] = job
            return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **kwargs) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in kwargs.items():
                setattr(job, key, value)
            job.updated_at = time.time()

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)


JOB_STORE = JobStore()
