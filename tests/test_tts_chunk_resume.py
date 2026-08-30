import tempfile
import unittest
from pathlib import Path

from split_text_for_tts import prepare_tts_chunks


class TtsChunkResumeTests(unittest.TestCase):
    def test_same_chunks_are_preserved_and_conflicts_are_not_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_file = root / "clean.txt"
            output_dir = root / "chunks"
            manifest = root / "manifest.json"
            input_file.write_text(
                "Birinci cümle. İkinci cümle. " * 30,
                encoding="utf-8",
            )

            first = prepare_tts_chunks(
                input_file=input_file,
                output_dir=output_dir,
                manifest_file=manifest,
                max_characters=300,
                min_characters=100,
            )
            original_bytes = {
                path.name: path.read_bytes()
                for path in first["saved_files"]
            }

            second = prepare_tts_chunks(
                input_file=input_file,
                output_dir=output_dir,
                manifest_file=manifest,
                max_characters=300,
                min_characters=100,
            )
            self.assertEqual(first["total_chunks"], second["total_chunks"])
            self.assertEqual(
                original_bytes,
                {path.name: path.read_bytes() for path in second["saved_files"]},
            )

            extra_file = output_dir / "tts_chunk_9999.txt"
            extra_file.write_text("Korunacak eski parça", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "hiçbir dosya silinmedi"):
                prepare_tts_chunks(
                    input_file=input_file,
                    output_dir=output_dir,
                    manifest_file=manifest,
                    max_characters=300,
                    min_characters=100,
                )
            self.assertTrue(extra_file.exists())


if __name__ == "__main__":
    unittest.main()