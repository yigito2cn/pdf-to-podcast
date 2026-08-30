import tempfile
import unittest
import wave
from pathlib import Path

from batch_gemini_tts import (
    GeminiTTSDailyQuotaError,
    cache_metadata_path,
    get_tts_cache_settings,
    validate_resumable_wav,
    write_cache_metadata,
)


def create_valid_wav(output_file: Path) -> None:
    with wave.open(str(output_file), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b"\x00\x00" * 2400)


class TtsResumeTests(unittest.TestCase):
    def test_daily_quota_exception_keeps_retry_after(self) -> None:
        error = GeminiTTSDailyQuotaError(
            Exception("tokens per day; try again in 7m49.152s")
        )
        self.assertEqual(str(error), "GEMINI_TTS_DAILY_QUOTA_EXHAUSTED")
        self.assertAlmostEqual(error.retry_after_seconds, 469.152)

    def test_legacy_wav_is_preserved_after_technical_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_file = Path(temporary_directory) / "chunk.wav"
            create_valid_wav(output_file)
            original_content = output_file.read_bytes()

            details, cache_status = validate_resumable_wav(
                source_text="Kaynak metin",
                output_file=output_file,
                model="gemini-tts",
                voice_name="Kore",
            )

            self.assertEqual(cache_status, "legacy_technical_only")
            self.assertGreater(details["duration_seconds"], 0)
            self.assertEqual(output_file.read_bytes(), original_content)

    def test_metadata_validates_source_model_and_voice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_file = Path(temporary_directory) / "chunk.wav"
            create_valid_wav(output_file)
            write_cache_metadata(
                metadata_file=cache_metadata_path(output_file),
                source_text="Kaynak metin",
                output_file=output_file,
                provider="gemini_tts",
                model="gemini-tts",
                settings=get_tts_cache_settings("Kore"),
            )

            _, cache_status = validate_resumable_wav(
                source_text="Kaynak metin",
                output_file=output_file,
                model="gemini-tts",
                voice_name="Kore",
            )
            self.assertEqual(cache_status, "metadata_validated")

            with self.assertRaisesRegex(ValueError, "settings_fingerprint"):
                validate_resumable_wav(
                    source_text="Kaynak metin",
                    output_file=output_file,
                    model="gemini-tts",
                    voice_name="Aoede",
                )


if __name__ == "__main__":
    unittest.main()