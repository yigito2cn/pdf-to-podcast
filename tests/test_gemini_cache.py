import tempfile
import unittest
from pathlib import Path

from pipeline.cache import (
    cache_metadata_path,
    validate_cached_output,
    write_cache_metadata,
)


class GeminiCacheTests(unittest.TestCase):
    def test_gemini_chunk_cache_rejects_changed_source_or_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_file = Path(temporary_directory) / "chunk_cleaned.txt"
            output_file.write_text("Temiz metin", encoding="utf-8")
            metadata_file = cache_metadata_path(output_file)
            settings = {"max_characters": 5000}
            write_cache_metadata(
                metadata_file=metadata_file,
                source_text="Kaynak metin",
                output_file=output_file,
                provider="gemini",
                model="gemini-model",
                settings=settings,
            )

            valid, reason = validate_cached_output(
                metadata_file=metadata_file,
                source_text="Değişmiş kaynak",
                output_file=output_file,
                provider="gemini",
                model="gemini-model",
                settings=settings,
            )
            self.assertFalse(valid)
            self.assertEqual(reason, "source_hash_mismatch")

            valid, reason = validate_cached_output(
                metadata_file=metadata_file,
                source_text="Kaynak metin",
                output_file=output_file,
                provider="gemini",
                model="başka-model",
                settings=settings,
            )
            self.assertFalse(valid)
            self.assertEqual(reason, "model_mismatch")


if __name__ == "__main__":
    unittest.main()