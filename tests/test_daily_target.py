import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import batch_gemini_tts
from batch_gemini_tts import validate_wav


class FakeClient:
    def close(self) -> None:
        pass


def fake_generate_audio(**kwargs):
    output_file = kwargs["output_file"]
    with wave.open(str(output_file), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b"\x00\x00" * 2400)
    return validate_wav(output_file)


class DailyTargetTests(unittest.TestCase):
    def test_daily_target_stops_after_new_chunk_and_creates_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_directory = root / "chunks"
            output_directory = root / "audio"
            input_directory.mkdir()
            for index in range(1, 4):
                (input_directory / f"tts_chunk_{index:04d}.txt").write_text(
                    "Bir iki üç dört beş.", encoding="utf-8"
                )
            final_file = root / "podcast.wav"
            report_file = root / "report.json"
            argv = [
                "batch_gemini_tts.py",
                "--input-dir", str(input_directory),
                "--output-dir", str(output_directory),
                "--final-file", str(final_file),
                "--report-file", str(report_file),
                "--target-minutes", "0.01",
                "--words-per-minute", "100",
            ]

            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), patch(
                "sys.argv", argv
            ), patch(
                "batch_gemini_tts.genai.Client", return_value=FakeClient()
            ), patch(
                "batch_gemini_tts.generate_audio",
                side_effect=fake_generate_audio,
            ):
                batch_gemini_tts.main()

            report = json.loads(report_file.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "daily_target_reached")
            self.assertTrue((root / "podcast_partial.wav").is_file())
            self.assertTrue((output_directory / "tts_chunk_0001.wav").is_file())
            self.assertFalse((output_directory / "tts_chunk_0002.wav").exists())
            self.assertFalse(final_file.exists())


if __name__ == "__main__":
    unittest.main()