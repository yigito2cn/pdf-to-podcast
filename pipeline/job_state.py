from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PIPELINE_VERSION = "1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def calculate_file_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()

    with file_path.open("rb") as source_file:
        for block in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


def create_job_id(
    pdf_sha256: str,
    start_page: int,
    end_page: int,
    pipeline_version: str = PIPELINE_VERSION,
) -> str:
    identity = (
        f"{pdf_sha256}:{start_page}:{end_page}:"
        f"{pipeline_version}"
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


@dataclass
class JobState:
    job_id: str
    pdf_file: str
    pdf_sha256: str
    start_page: int
    end_page: int
    status: str = "created"
    stage: str = "created"
    pipeline_version: str = PIPELINE_VERSION
    text_provider_primary: str = "gemini"
    text_provider_fallback: str = "groq"
    tts_provider_primary: str = "gemini"
    tts_provider_fallback: str = "edge"
    text_chunks_total: int = 0
    text_chunks_completed: int = 0
    tts_chunks_total: int = 0
    tts_chunks_completed: int = 0
    paused_at: str | None = None
    paused_provider: str | None = None
    resume_after: str | None = None
    last_error_type: str | None = None
    last_error_message: str | None = None
    outputs: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobState":
        known_fields = cls.__dataclass_fields__
        return cls(**{
            key: value
            for key, value in data.items()
            if key in known_fields
        })


class JobStateStore:
    def __init__(self, work_directory: Path) -> None:
        self.work_directory = work_directory
        self.status_file = work_directory / "job_status.json"

    def load(self) -> JobState:
        data = json.loads(self.status_file.read_text(encoding="utf-8"))
        return JobState.from_dict(data)

    def load_or_create(self, initial_state: JobState) -> JobState:
        if self.status_file.exists():
            return self.load()

        self.save(initial_state)
        return initial_state

    def save(self, state: JobState) -> None:
        self.work_directory.mkdir(parents=True, exist_ok=True)
        state.updated_at = utc_now()
        temporary_file = self.status_file.with_suffix(".json.tmp")
        temporary_file.write_text(
            json.dumps(
                asdict(state),
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        temporary_file.replace(self.status_file)

    def checkpoint(
        self,
        state: JobState,
        *,
        status: str | None = None,
        stage: str | None = None,
        **changes: Any,
    ) -> None:
        if status is not None:
            state.status = status
        if stage is not None:
            state.stage = stage

        for key, value in changes.items():
            if key not in state.__dataclass_fields__:
                raise ValueError(f"Bilinmeyen job state alanı: {key}")
            setattr(state, key, value)

        self.save(state)

    def pause_for_quota(
        self,
        state: JobState,
        *,
        stage: str,
        provider: str,
        paused_at: str,
        resume_after: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.checkpoint(
            state,
            status="paused_quota",
            stage=stage,
            paused_at=paused_at,
            paused_provider=provider,
            resume_after=resume_after,
            last_error_type="daily_quota",
            last_error_message=error_message,
        )
