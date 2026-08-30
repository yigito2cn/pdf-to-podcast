import tempfile
import unittest
import wave
from pathlib import Path

from batch_gemini_tts import combine_wav_files, validate_wav


def create_wav(path: Path, frames: int) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b"\x00\x00" * frames)


class PartialAudioTests(unittest.TestCase):
    def test_completed_wavs_can_form_atomic_partial_podcast(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first = root / "chunk_1.wav"
            second = root / "chunk_2.wav"
            output = root / "podcast_partial.wav"
            create_wav(first, 2400)
            create_wav(second, 2400)

            details = combine_wav_files(
                wav_files=[first, second],
                output_file=output,
                silence_ms=100,
            )

            self.assertTrue(output.is_file())
            self.assertFalse(output.with_suffix(".wav.tmp").exists())
            self.assertGreater(details["duration_seconds"], 0.2)
            self.assertEqual(validate_wav(output)["sample_rate"], 24000)


if __name__ == "__main__":
    unittest.main()