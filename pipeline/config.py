from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PRESERVE_API_KEYS_ENV = "PDF_TO_PODCAST_PRESERVE_API_KEYS"


def load_project_environment(dotenv_path: Path | None = None) -> bool:
    preserve_api_keys = os.getenv(PRESERVE_API_KEYS_ENV) == "1"
    return load_dotenv(
        dotenv_path=dotenv_path,
        override=not preserve_api_keys,
    )
