import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.job_state import JobState
from podcast_from_pdf import resolve_output_directory


class OutputDirectoryTests(unittest.TestCase):
    def test_page_range_names_output_and_preserves_legacy_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            work_directory = root / "work" / "job-hash"
            legacy_output = root / "output" / "job-hash"
            work_directory.mkdir(parents=True)
            legacy_output.mkdir(parents=True)
            legacy_file = legacy_output / "clean_text.txt"
            legacy_file.write_text("Korunacak metin", encoding="utf-8")
            state = JobState(
                job_id="job-hash",
                pdf_file="book.pdf",
                pdf_sha256="abc123",
                start_page=53,
                end_page=75,
            )

            with patch("podcast_from_pdf.OUTPUT_ROOT", root / "output"):
                output_directory = resolve_output_directory(
                    state=state,
                    work_directory=work_directory,
                )

            self.assertEqual(output_directory.name, "53-75")
            self.assertEqual(
                (output_directory / "clean_text.txt").read_text(
                    encoding="utf-8"
                ),
                "Korunacak metin",
            )
            self.assertEqual(
                legacy_file.read_text(encoding="utf-8"),
                "Korunacak metin",
            )

    def test_existing_page_range_file_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            work_directory = root / "work" / "job-hash"
            legacy_output = root / "output" / "job-hash"
            page_output = root / "output" / "8-10"
            work_directory.mkdir(parents=True)
            legacy_output.mkdir(parents=True)
            page_output.mkdir(parents=True)
            (legacy_output / "clean_text.txt").write_text(
                "Eski metin", encoding="utf-8"
            )
            target_file = page_output / "clean_text.txt"
            target_file.write_text("Yeni metin", encoding="utf-8")
            state = JobState(
                job_id="job-hash",
                pdf_file="book.pdf",
                pdf_sha256="abc123",
                start_page=8,
                end_page=10,
            )

            with patch("podcast_from_pdf.OUTPUT_ROOT", root / "output"):
                resolve_output_directory(
                    state=state,
                    work_directory=work_directory,
                )

            self.assertEqual(
                target_file.read_text(encoding="utf-8"),
                "Yeni metin",
            )


if __name__ == "__main__":
    unittest.main()