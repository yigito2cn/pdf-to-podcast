import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.job_state import JobState, JobStateStore
from podcast_from_pdf import run_tts_stage


class UnifiedTtsStageTests(unittest.TestCase):
    def test_changed_clean_text_uses_hashed_chunk_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            work_directory = root / "work"
            output_directory = root / "output"
            old_chunk_directory = work_directory / "tts_chunks"
            old_chunk_directory.mkdir(parents=True)
            output_directory.mkdir()
            old_chunk = old_chunk_directory / "tts_chunk_0001.txt"
            old_chunk.write_text("Korunacak eski metin", encoding="utf-8")
            clean_text = output_directory / "clean_text.txt"
            clean_text.write_text("Yeni Türkçe metin. " * 50, encoding="utf-8")
            store = JobStateStore(work_directory)
            state = JobState(
                job_id="job-1",
                pdf_file="book.pdf",
                pdf_sha256="abc123",
                start_page=1,
                end_page=1,
                status="text_completed",
            )
            store.save(state)
            process_result = type("Result", (), {"returncode": 0})()

            with patch(
                "podcast_from_pdf.subprocess.run",
                return_value=process_result,
            ) as run_mock:
                run_tts_stage(
                    clean_text_file=clean_text,
                    work_directory=work_directory,
                    output_directory=output_directory,
                    state_store=store,
                    state=state,
                )

            self.assertEqual(
                old_chunk.read_text(encoding="utf-8"),
                "Korunacak eski metin",
            )
            command = run_mock.call_args.args[0]
            input_directory = Path(command[command.index("--input-dir") + 1])
            self.assertRegex(input_directory.name, r"^tts_chunks_[0-9a-f]{12}$")
            self.assertTrue((input_directory / "tts_chunk_0001.txt").is_file())

    def test_tts_failure_keeps_text_and_marks_text_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            work_directory = root / "work"
            output_directory = root / "output"
            work_directory.mkdir()
            output_directory.mkdir()
            clean_text = output_directory / "clean_text.txt"
            clean_text.write_text(
                "Bu metin ses üretilemese de korunur. " * 30,
                encoding="utf-8",
            )
            store = JobStateStore(work_directory)
            state = JobState(
                job_id="job-1",
                pdf_file="book.pdf",
                pdf_sha256="abc123",
                start_page=1,
                end_page=1,
                status="text_completed",
                outputs={"clean_text": str(clean_text)},
            )
            store.save(state)

            process_result = type("Result", (), {"returncode": 1})()
            with patch(
                "podcast_from_pdf.subprocess.run",
                return_value=process_result,
            ) as run_mock, patch(
                "podcast_from_pdf.generate_edge_tts",
                side_effect=RuntimeError("Edge unavailable"),
            ):
                return_code = run_tts_stage(
                    clean_text_file=clean_text,
                    work_directory=work_directory,
                    output_directory=output_directory,
                    state_store=store,
                    state=state,
                )

            self.assertEqual(return_code, 1)
            self.assertEqual(store.load().status, "completed_text_only")
            self.assertTrue(clean_text.exists())
            command = run_mock.call_args.args[0]
            self.assertIn("--job-dir", command)
            self.assertNotIn("GEMINI_API_KEY", " ".join(command))

    def test_edge_fallback_completes_job_after_gemini_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            work_directory = root / "work"
            output_directory = root / "output"
            work_directory.mkdir()
            output_directory.mkdir()
            clean_text = output_directory / "clean_text.txt"
            clean_text.write_text("Türkçe metin. " * 50, encoding="utf-8")
            store = JobStateStore(work_directory)
            state = JobState(
                job_id="job-1",
                pdf_file="book.pdf",
                pdf_sha256="abc123",
                start_page=1,
                end_page=1,
                status="text_completed",
            )
            store.save(state)
            process_result = type("Result", (), {"returncode": 1})()

            def fake_edge(**kwargs):
                kwargs["final_file"].write_bytes(b"ID3" + b"\x00" * 200)
                return {
                    "total_chunks": 1,
                    "source_total_chunks": 1,
                    "completed_chunks": 1,
                    "final_file": str(kwargs["final_file"]),
                }

            with patch(
                "podcast_from_pdf.subprocess.run",
                return_value=process_result,
            ), patch(
                "podcast_from_pdf.generate_edge_tts",
                side_effect=fake_edge,
            ):
                run_tts_stage(
                    clean_text_file=clean_text,
                    work_directory=work_directory,
                    output_directory=output_directory,
                    state_store=store,
                    state=state,
                )

            reloaded = store.load()
            self.assertEqual(reloaded.status, "completed")
            self.assertTrue(Path(reloaded.outputs["podcast"]).is_file())

    def test_target_minutes_is_forwarded_to_edge_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            work_directory = root / "work"
            output_directory = root / "output"
            work_directory.mkdir()
            output_directory.mkdir()
            clean_text = output_directory / "clean_text.txt"
            clean_text.write_text("Türkçe metin. " * 50, encoding="utf-8")
            store = JobStateStore(work_directory)
            state = JobState(
                job_id="job-1",
                pdf_file="book.pdf",
                pdf_sha256="abc123",
                start_page=1,
                end_page=1,
                status="text_completed",
            )
            store.save(state)
            process_result = type("Result", (), {"returncode": 1})()

            with patch(
                "podcast_from_pdf.subprocess.run",
                return_value=process_result,
            ), patch(
                "podcast_from_pdf.generate_edge_tts",
                side_effect=RuntimeError("Edge unavailable"),
            ) as edge_mock:
                run_tts_stage(
                    clean_text_file=clean_text,
                    work_directory=work_directory,
                    output_directory=output_directory,
                    state_store=store,
                    state=state,
                    target_minutes=15,
                    words_per_minute=140,
                )

            self.assertEqual(edge_mock.call_args.kwargs["target_minutes"], 15)
            self.assertEqual(edge_mock.call_args.kwargs["words_per_minute"], 140)
            self.assertEqual(store.load().status, "completed_text_only")


if __name__ == "__main__":
    unittest.main()