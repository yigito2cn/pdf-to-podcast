import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.job_state import JobState, JobStateStore
from podcast_from_pdf import run_gemini_stage


class PipelineOutputTests(unittest.TestCase):
    def test_text_stage_always_creates_txt_and_readaloud_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            work_directory = root / "work"
            output_directory = root / "output"
            work_directory.mkdir()
            precleaned_file = work_directory / "precleaned_text.txt"
            precleaned_file.write_text(
                "Türkçe deneme metni.", encoding="utf-8"
            )
            store = JobStateStore(work_directory)
            state = JobState(
                job_id="job-1",
                pdf_file="book.pdf",
                pdf_sha256="abc123",
                start_page=1,
                end_page=1,
            )
            store.save(state)

            def fake_cleaner(**kwargs):
                kwargs["output_file"].parent.mkdir(
                    parents=True, exist_ok=True
                )
                kwargs["output_file"].write_text(
                    "Temiz Türkçe metin.", encoding="utf-8"
                )
                kwargs["state"].outputs["clean_text"] = str(
                    kwargs["output_file"]
                )
                kwargs["state_store"].checkpoint(
                    kwargs["state"],
                    status="text_completed",
                    stage="text_completed",
                )
                return {
                    "status": "completed",
                    "provider": "mock",
                    "total_chunks": 1,
                }

            with patch(
                "podcast_from_pdf.clean_text_with_fallback",
                side_effect=fake_cleaner,
            ):
                text_file = run_gemini_stage(
                    precleaned_text_file=precleaned_file,
                    work_directory=work_directory,
                    output_directory=output_directory,
                    force=False,
                    state_store=store,
                    state=state,
                )

            pdf_file = output_directory / "readaloud.pdf"
            self.assertEqual(
                text_file.read_text(encoding="utf-8"),
                "Temiz Türkçe metin.",
            )
            self.assertTrue(pdf_file.is_file())
            self.assertGreater(pdf_file.stat().st_size, 0)
            self.assertFalse(
                pdf_file.with_suffix(".pdf.tmp").exists()
            )
            self.assertEqual(
                store.load().outputs["readaloud_pdf"],
                str(pdf_file),
            )


if __name__ == "__main__":
    unittest.main()