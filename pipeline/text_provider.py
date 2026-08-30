from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from pipeline.gemini_text_cleaner import (
    GeminiQuotaError,
    clean_text_file_with_gemini,
)
from pipeline.groq_text_cleaner import (
    GroqAuthenticationError,
    GroqModelError,
    GroqQuotaError,
    clean_text_file_with_groq,
    read_cleaned_candidate,
)
from pipeline.job_state import JobState, JobStateStore
from pipeline.provider_errors import (
    ProviderErrorType,
    classify_provider_error,
    parse_retry_after_seconds,
)


Cleaner = Callable[..., dict]


def write_text_atomic(output_file: Path, text: str) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = output_file.with_suffix(output_file.suffix + ".tmp")
    temporary_file.write_text(text, encoding="utf-8")
    temporary_file.replace(output_file)


def build_best_available_text(
    *,
    input_file: Path,
    work_directory: Path,
    output_file: Path,
) -> dict:
    source_directory = work_directory / "gemini_text_chunks"
    if not source_directory.exists():
        source_directory = work_directory / "groq_text_chunks"

    source_files = sorted(source_directory.glob("chunk_*.txt"))
    if not source_files:
        source_text = input_file.read_text(encoding="utf-8-sig").strip()
        write_text_atomic(output_file, source_text)
        return {
            "output_file": str(output_file),
            "total_chunks": 1,
            "cleaned_chunks": 0,
            "source_fallback_chunks": 1,
        }

    selected_parts = []
    cleaned_count = 0
    source_fallback_count = 0

    for source_file in source_files:
        source_text = source_file.read_text(encoding="utf-8-sig").strip()
        suffix = source_file.stem.removeprefix("chunk_")
        candidate_files = (
            work_directory
            / "gemini_cleaned_chunks"
            / f"chunk_{suffix}_cleaned.txt",
            work_directory
            / "groq_cleaned_chunks"
            / f"chunk_{suffix}_cleaned.txt",
        )

        selected_text = None
        for candidate_file in candidate_files:
            candidate, _ = read_cleaned_candidate(
                candidate_file=candidate_file,
                original_chunk=source_text,
            )
            if candidate:
                selected_text = candidate
                cleaned_count += 1
                break

        if selected_text is None:
            selected_text = source_text
            source_fallback_count += 1

        selected_parts.append(selected_text)

    final_text = "\n\n".join(selected_parts).strip()
    write_text_atomic(output_file, final_text)
    return {
        "output_file": str(output_file),
        "total_chunks": len(source_files),
        "cleaned_chunks": cleaned_count,
        "source_fallback_chunks": source_fallback_count,
    }


def clean_text_with_fallback(
    *,
    input_file: Path,
    output_file: Path,
    work_directory: Path,
    state_store: JobStateStore | None = None,
    state: JobState | None = None,
    gemini_cleaner: Cleaner = clean_text_file_with_gemini,
    groq_cleaner: Cleaner = clean_text_file_with_groq,
) -> dict:
    if state_store is not None and state is not None:
        state_store.checkpoint(
            state,
            status="text_cleaning",
            stage="text_cleaning",
        )

    gemini_output = work_directory / "gemini_cleaned_text.txt"

    try:
        result = gemini_cleaner(
            input_file=input_file,
            output_file=gemini_output,
            work_directory=work_directory,
        )
        write_text_atomic(
            output_file,
            gemini_output.read_text(encoding="utf-8-sig").strip(),
        )
        result = {**result, "provider": "gemini"}
    except Exception as gemini_error:
        gemini_error_type = classify_provider_error(gemini_error)
        can_fallback = (
            isinstance(gemini_error, GeminiQuotaError)
            or gemini_error_type
            in {
                ProviderErrorType.DAILY_QUOTA,
                ProviderErrorType.RATE_LIMIT,
                ProviderErrorType.AUTHENTICATION,
                ProviderErrorType.MODEL_NOT_FOUND,
                ProviderErrorType.TEMPORARY,
            }
        )
        if not can_fallback:
            raise

        try:
            result = groq_cleaner(
                input_file=input_file,
                output_file=output_file,
                work_directory=work_directory,
            )
            result = {**result, "provider": "groq"}
        except GroqQuotaError as error:
            partial_result = build_best_available_text(
                input_file=input_file,
                work_directory=work_directory,
                output_file=output_file,
            )
            if state_store is not None and state is not None:
                retry_after_seconds = parse_retry_after_seconds(error)
                resume_after = None
                if retry_after_seconds is not None:
                    resume_after = (
                        datetime.now(timezone.utc)
                        + timedelta(seconds=retry_after_seconds)
                    ).isoformat()
                state_store.pause_for_quota(
                    state,
                    stage="text_cleaning",
                    provider="groq",
                    paused_at=getattr(
                        error,
                        "paused_at",
                        "next_incomplete_text_chunk",
                    ),
                    resume_after=resume_after,
                    error_message="Groq günlük kotası doldu.",
                )
                state.text_chunks_total = partial_result[
                    "total_chunks"
                ]
                state.text_chunks_completed = partial_result[
                    "cleaned_chunks"
                ]
                state.outputs["clean_text"] = str(output_file)
                state_store.save(state)

            return {
                **partial_result,
                "provider": "mixed_partial",
                "status": "paused_quota",
            }
        except (GroqAuthenticationError, GroqModelError) as error:
            partial_result = build_best_available_text(
                input_file=input_file,
                work_directory=work_directory,
                output_file=output_file,
            )
            if state_store is not None and state is not None:
                state.outputs["clean_text"] = str(output_file)
                state.last_error_type = classify_provider_error(error).value
                state.last_error_message = (
                    "Groq sağlayıcı yapılandırması kullanılamıyor."
                )
                state_store.checkpoint(
                    state,
                    status="completed_text_only",
                    stage="completed_text_only",
                )
            return {
                **partial_result,
                "provider": "preclean_fallback",
                "status": "completed_text_only",
            }

    if state_store is not None and state is not None:
        state.text_chunks_total = result.get("total_chunks", 0)
        state.text_chunks_completed = result.get("total_chunks", 0)
        state.outputs["clean_text"] = str(output_file)
        state_store.checkpoint(
            state,
            status="text_completed",
            stage="text_completed",
            paused_at=None,
            paused_provider=None,
            resume_after=None,
            last_error_type=None,
            last_error_message=None,
        )

    return {**result, "status": "completed"}
