import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import batch_gemini_tts


def create_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b"\x00\x00" * 2400)


class FakeClient:
    def close(self) -> None:
        pass


class TtsLimitTests(unittest.TestCase):
    def test_limit_does_not_create_false_final_or_completed_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_directory = root / "chunks"
            output_directory = root / "audio"
            input_directory.mkdir()
            output_directory.mkdir()
            for index in range(1, 4):
                (input_directory / f"tts_chunk_{index:04d}.txt").write_text(
                    f"Türkçe metin {index}.", encoding="utf-8"
                )
            create_wav(output_directory / "tts_chunk_0001.wav")
            create_wav(output_directory / "tts_chunk_0002.wav")
            final_file = root / "final.wav"
            report_file = root / "report.json"
            argv = [
                "batch_gemini_tts.py",
                "--input-dir",
                str(input_directory),
                "--output-dir",
                str(output_directory),
                "--final-file",
                str(final_file),
                "--report-file",
                str(report_file),
                "--limit",
                "2",
            ]

            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), patch(
                "sys.argv", argv
            ), patch(
                "batch_gemini_tts.genai.Client", return_value=FakeClient()
            ):
                batch_gemini_tts.main()

            report = json.loads(report_file.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "incomplete")
            self.assertEqual(len(report["chunks"]), 2)
            self.assertFalse(final_file.exists())
            self.assertTrue(
                (output_directory / "tts_chunk_0001.wav").exists()
            )


if __name__ == "__main__":
    unittest.main()