from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def calculate_bytes_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def calculate_text_sha256(text: str) -> str:
    return calculate_bytes_sha256(text.encode("utf-8"))


def calculate_file_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()

    with file_path.open("rb") as source_file:
        for block in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


def create_settings_fingerprint(settings: dict[str, Any]) -> str:
    serialized = json.dumps(
        settings,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return calculate_text_sha256(serialized)


def cache_metadata_path(output_file: Path) -> Path:
    return output_file.with_suffix(output_file.suffix + ".metadata.json")


def write_cache_metadata(
    *,
    metadata_file: Path,
    source_text: str,
    output_file: Path,
    provider: str,
    model: str,
    settings: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not output_file.is_file():
        raise FileNotFoundError(f"Cache çıktısı bulunamadı: {output_file}")

    normalized_settings = settings or {}
    metadata = {
        "source_hash": calculate_text_sha256(source_text),
        "output_hash": calculate_file_sha256(output_file),
        "provider": provider,
        "model": model,
        "settings": normalized_settings,
        "settings_fingerprint": create_settings_fingerprint(
            normalized_settings
        ),
        "validation": validation or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = metadata_file.with_suffix(metadata_file.suffix + ".tmp")
    temporary_file.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_file.replace(metadata_file)
    return metadata


def validate_cached_output(
    *,
    metadata_file: Path,
    source_text: str,
    output_file: Path,
    provider: str,
    model: str,
    settings: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    if not output_file.is_file():
        return False, "output_missing"
    if not metadata_file.is_file():
        return False, "metadata_missing"

    try:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "metadata_invalid"

    expected = {
        "source_hash": calculate_text_sha256(source_text),
        "provider": provider,
        "model": model,
        "settings_fingerprint": create_settings_fingerprint(settings or {}),
    }

    for key, expected_value in expected.items():
        if metadata.get(key) != expected_value:
            return False, f"{key}_mismatch"

    try:
        output_hash = calculate_file_sha256(output_file)
    except OSError:
        return False, "output_unreadable"

    if metadata.get("output_hash") != output_hash:
        return False, "output_hash_mismatch"

    return True, "valid"
