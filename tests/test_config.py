import os
import unittest
from unittest.mock import patch

from pipeline.config import (
    PRESERVE_API_KEYS_ENV,
    load_project_environment,
)


class ConfigTests(unittest.TestCase):
    def test_cli_keys_are_not_overridden_by_dotenv(self) -> None:
        with patch.dict(
            os.environ,
            {PRESERVE_API_KEYS_ENV: "1", "GEMINI_API_KEY": "cli-key"},
            clear=True,
        ), patch("pipeline.config.load_dotenv", return_value=True) as loader:
            load_project_environment()

        loader.assert_called_once_with(dotenv_path=None, override=False)

    def test_direct_scripts_keep_legacy_dotenv_override(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "pipeline.config.load_dotenv", return_value=True
        ) as loader:
            load_project_environment()

        loader.assert_called_once_with(dotenv_path=None, override=True)


if __name__ == "__main__":
    unittest.main()