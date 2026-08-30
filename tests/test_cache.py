import tempfile
import unittest
from pathlib import Path

from pipeline.cache import (
    cache_metadata_path,
    validate_cached_output,
    write_cache_metadata,
)


class CacheTests(unittest.TestCase):
    def test_cache_requires_matching_source_model_settings_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_file = Path(temporary_directory) / "chunk.wav"
            output_file.write_bytes(b"valid wav placeholder")
            metadata_file = cache_metadata_path(output_file)
            settings = {"voice": "Kore", "sample_rate": 24000}

            write_cache_metadata(
                metadata_file=metadata_file,
                source_text="Kaynak metin",
                output_file=output_file,
                provider="gemini_tts",
                model="gemini-tts",
                settings=settings,
            )

            valid, reason = validate_cached_output(
                metadata_file=metadata_file,
                source_text="Kaynak metin",
                output_file=output_file,
                provider="gemini_tts",
                model="gemini-tts",
                settings=settings,
            )
            self.assertTrue(valid)
            self.assertEqual(reason, "valid")

            changed_voice = {"voice": "Aoede", "sample_rate": 24000}
            valid, reason = validate_cached_output(
                metadata_file=metadata_file,
                source_text="Kaynak metin",
                output_file=output_file,
                provider="gemini_tts",
                model="gemini-tts",
                settings=changed_voice,
            )
            self.assertFalse(valid)
            self.assertEqual(reason, "settings_fingerprint_mismatch")

            output_file.write_bytes(b"changed output")
            valid, reason = validate_cached_output(
                metadata_file=metadata_file,
                source_text="Kaynak metin",
                output_file=output_file,
                provider="gemini_tts",
                model="gemini-tts",
                settings=settings,
            )
            self.assertFalse(valid)
            self.assertEqual(reason, "output_hash_mismatch")

    def test_legacy_output_is_preserved_when_metadata_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_file = Path(temporary_directory) / "legacy.txt"
            output_file.write_text("Tamamlanmış çıktı", encoding="utf-8")

            valid, reason = validate_cached_output(
                metadata_file=cache_metadata_path(output_file),
                source_text="Kaynak",
                output_file=output_file,
                provider="groq",
                model="model",
            )

            self.assertFalse(valid)
            self.assertEqual(reason, "metadata_missing")
            self.assertEqual(
                output_file.read_text(encoding="utf-8"),
                "Tamamlanmış çıktı",
            )


if __name__ == "__main__":
    unittest.main()