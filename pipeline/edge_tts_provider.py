from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Callable

import edge_tts

from pipeline.cache import (
    cache_metadata_path,
    validate_cached_output,
    write_cache_metadata,
)


DEFAULT_EDGE_VOICE = "tr-TR-EmelNeural"


class EdgeTTSPartialError(RuntimeError):
    def __init__(
        self,
        provider_error: Exception,
        partial_file: Path,
        completed_chunks: int,
    ) -> None:
        super().__init__(f"{type(provider_error).__name__}: {provider_error}")
        self.partial_file = partial_file
        self.completed_chunks = completed_chunks


def select_text_files(
    *,
    input_directory: Path,
    output_directory: Path,
    limit: int | None = None,
    target_minutes: float | None = None,
    words_per_minute: float = 150.0,
) -> list[Path]:
    text_files = sorted(input_directory.glob("tts_chunk_*.txt"))
    if limit is not None:
        return text_files[:limit]
    if target_minutes is None:
        return text_files

    target_words = target_minutes * words_per_minute
    selected_files = []
    selected_missing_words = 0
    for text_file in text_files:
        output_file = output_directory / f"{text_file.stem}.mp3"
        if not output_file.exists() and selected_missing_words >= target_words:
            break
        selected_files.append(text_file)
        if not output_file.exists():
            selected_missing_words += len(
                text_file.read_text(encoding="utf-8-sig").split()
            )
    return selected_files


def validate_mp3(mp3_file: Path) -> dict:
    if not mp3_file.is_file():
        raise FileNotFoundError(f"MP3 bulunamadı: {mp3_file}")
    file_size = mp3_file.stat().st_size
    if file_size < 100:
        raise ValueError(f"MP3 çok küçük veya boş: {mp3_file}")
    return {"file_size_bytes": file_size}


def find_valid_edge_audio(
    *,
    text_files: list[Path],
    output_directory: Path,
    voice: str,
) -> list[Path]:
    valid_files = []
    settings = {"voice": voice}
    for text_file in text_files:
        source_text = text_file.read_text(encoding="utf-8-sig").strip()
        output_file = output_directory / f"{text_file.stem}.mp3"
        cache_valid, _ = validate_cached_output(
            metadata_file=cache_metadata_path(output_file),
            source_text=source_text,
            output_file=output_file,
            provider="edge_tts",
            model="edge_read_aloud",
            settings=settings,
        )
        if not cache_valid:
            break
        validate_mp3(output_file)
        valid_files.append(output_file)
    return valid_files


def merge_mp3_files(mp3_files: list[Path], output_file: Path) -> None:
    if not mp3_files:
        raise ValueError("Birleştirilecek Edge TTS MP3 dosyası yok.")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    list_file = output_file.with_suffix(".concat.txt")
    temporary_file = output_file.with_name(output_file.stem + ".tmp.mp3")
    lines = []
    for mp3_file in mp3_files:
        escaped_path = mp3_file.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{escaped_path}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    try:
        completed = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c",
                "copy",
                "-y",
                str(temporary_file),
            ],
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Edge TTS MP3 birleştirme başarısız: {completed.returncode}"
            )
        validate_mp3(temporary_file)
        temporary_file.replace(output_file)
    finally:
        list_file.unlink(missing_ok=True)
        temporary_file.unlink(missing_ok=True)


async def _generate_edge_chunks(
    *,
    text_files: list[Path],
    output_directory: Path,
    voice: str,
    communicate_factory: Callable = edge_tts.Communicate,
) -> list[Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    mp3_files = []
    settings = {"voice": voice}

    for text_file in text_files:
        source_text = text_file.read_text(encoding="utf-8-sig").strip()
        if not source_text:
            raise ValueError(f"Edge TTS metin parçası boş: {text_file}")

        output_file = output_directory / f"{text_file.stem}.mp3"
        metadata_file = cache_metadata_path(output_file)
        cache_valid, _ = validate_cached_output(
            metadata_file=metadata_file,
            source_text=source_text,
            output_file=output_file,
            provider="edge_tts",
            model="edge_read_aloud",
            settings=settings,
        )
        if cache_valid:
            validate_mp3(output_file)
            mp3_files.append(output_file)
            continue

        if output_file.exists():
            if not metadata_file.exists():
                validate_mp3(output_file)
                mp3_files.append(output_file)
                continue
            raise RuntimeError(
                "Edge TTS cache metadata uyuşmuyor; mevcut MP3 korundu: "
                f"{output_file}"
            )

        temporary_file = output_file.with_suffix(".mp3.tmp")
        communicator = communicate_factory(source_text, voice)
        try:
            await communicator.save(str(temporary_file))
            validate_mp3(temporary_file)
            temporary_file.replace(output_file)
        finally:
            temporary_file.unlink(missing_ok=True)

        write_cache_metadata(
            metadata_file=metadata_file,
            source_text=source_text,
            output_file=output_file,
            provider="edge_tts",
            model="edge_read_aloud",
            settings=settings,
            validation=validate_mp3(output_file),
        )
        mp3_files.append(output_file)

    return mp3_files


def generate_edge_tts(
    *,
    input_directory: Path,
    output_directory: Path,
    final_file: Path,
    voice: str = DEFAULT_EDGE_VOICE,
    limit: int | None = None,
    target_minutes: float | None = None,
    words_per_minute: float = 150.0,
    communicate_factory: Callable = edge_tts.Communicate,
    merger: Callable[[list[Path], Path], None] = merge_mp3_files,
) -> dict:
    source_total_chunks = len(
        list(input_directory.glob("tts_chunk_*.txt"))
    )
    text_files = select_text_files(
        input_directory=input_directory,
        output_directory=output_directory,
        limit=limit,
        target_minutes=target_minutes,
        words_per_minute=words_per_minute,
    )
    if not text_files:
        raise FileNotFoundError(
            f"Edge TTS metin parçası bulunamadı: {input_directory}"
        )

    try:
        mp3_files = asyncio.run(
            _generate_edge_chunks(
                text_files=text_files,
                output_directory=output_directory,
                voice=voice,
                communicate_factory=communicate_factory,
            )
        )
    except Exception as error:
        mp3_files = find_valid_edge_audio(
            text_files=text_files,
            output_directory=output_directory,
            voice=voice,
        )
        if not mp3_files:
            raise
        partial_file = final_file.with_name(
            final_file.stem + "_partial" + final_file.suffix
        )
        merger(mp3_files, partial_file)
        raise EdgeTTSPartialError(
            error,
            partial_file,
            len(mp3_files),
        ) from error
    merger(mp3_files, final_file)
    return {
        "provider": "edge_tts",
        "voice": voice,
        "total_chunks": len(text_files),
        "source_total_chunks": source_total_chunks,
        "completed_chunks": len(mp3_files),
        "all_chunks_completed": len(text_files) == source_total_chunks,
        "final_file": str(final_file),
    }
