import tempfile
import unittest
from pathlib import Path

from pipeline.gemini_text_cleaner import GeminiQuotaError
from pipeline.groq_text_cleaner import GroqQuotaError
from pipeline.groq_text_cleaner import GroqAuthenticationError
from pipeline.job_state import JobState, JobStateStore
from pipeline.text_provider import clean_text_with_fallback


class TextProviderTests(unittest.TestCase):
    def create_state(self, work_directory: Path) -> tuple[JobStateStore, JobState]:
        store = JobStateStore(work_directory)
        state = JobState(
            job_id="job-1",
            pdf_file="book.pdf",
            pdf_sha256="abc123",
            start_page=1,
            end_page=2,
        )
        store.save(state)
        return store, state

    def test_groq_completes_after_gemini_quota(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            input_file = work_directory / "precleaned.txt"
            output_file = work_directory / "clean.txt"
            input_file.write_text("Kaynak metin", encoding="utf-8")
            store, state = self.create_state(work_directory)

            def gemini_cleaner(**_kwargs):
                raise GeminiQuotaError("Gemini daily quota")

            def groq_cleaner(**kwargs):
                kwargs["output_file"].write_text(
                    "Groq temiz metin", encoding="utf-8"
                )
                return {"total_chunks": 1}

            result = clean_text_with_fallback(
                input_file=input_file,
                output_file=output_file,
                work_directory=work_directory,
                state_store=store,
                state=state,
                gemini_cleaner=gemini_cleaner,
                groq_cleaner=groq_cleaner,
            )

            self.assertEqual(result["provider"], "groq")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(store.load().status, "text_completed")
            self.assertEqual(
                output_file.read_text(encoding="utf-8"),
                "Groq temiz metin",
            )

    def test_groq_is_used_after_invalid_gemini_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            input_file = work_directory / "precleaned.txt"
            output_file = work_directory / "clean.txt"
            input_file.write_text("Kaynak metin", encoding="utf-8")

            def gemini_cleaner(**_kwargs):
                raise RuntimeError("GEMINI_API_KEY bulunamadı")

            def groq_cleaner(**kwargs):
                kwargs["output_file"].write_text("Groq metni", encoding="utf-8")
                return {"total_chunks": 1}

            result = clean_text_with_fallback(
                input_file=input_file,
                output_file=output_file,
                work_directory=work_directory,
                gemini_cleaner=gemini_cleaner,
                groq_cleaner=groq_cleaner,
            )

            self.assertEqual(result["provider"], "groq")
            self.assertEqual(result["status"], "completed")

    def test_missing_both_keys_keeps_precleaned_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            input_file = work_directory / "precleaned.txt"
            output_file = work_directory / "clean.txt"
            input_file.write_text("Korunacak kaynak", encoding="utf-8")
            store, state = self.create_state(work_directory)

            def gemini_cleaner(**_kwargs):
                raise RuntimeError("GEMINI_API_KEY bulunamadı")

            def groq_cleaner(**_kwargs):
                raise GroqAuthenticationError(
                    "GROQ_API_KEY ortam değişkeni bulunamadı"
                )

            result = clean_text_with_fallback(
                input_file=input_file,
                output_file=output_file,
                work_directory=work_directory,
                state_store=store,
                state=state,
                gemini_cleaner=gemini_cleaner,
                groq_cleaner=groq_cleaner,
            )

            self.assertEqual(result["status"], "completed_text_only")
            self.assertEqual(store.load().status, "completed_text_only")
            self.assertEqual(
                output_file.read_text(encoding="utf-8"),
                "Korunacak kaynak",
            )

    def test_two_quota_errors_leave_text_and_paused_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            input_file = work_directory / "precleaned.txt"
            output_file = work_directory / "clean.txt"
            input_file.write_text("Birinci.\n\nİkinci.", encoding="utf-8")
            source_directory = work_directory / "gemini_text_chunks"
            cleaned_directory = work_directory / "gemini_cleaned_chunks"
            source_directory.mkdir()
            cleaned_directory.mkdir()
            (source_directory / "chunk_0001.txt").write_text(
                "Birinci.", encoding="utf-8"
            )
            (source_directory / "chunk_0002.txt").write_text(
                "İkinci.", encoding="utf-8"
            )
            (cleaned_directory / "chunk_0001_cleaned.txt").write_text(
                "Birinci.", encoding="utf-8"
            )
            store, state = self.create_state(work_directory)

            def gemini_cleaner(**_kwargs):
                raise GeminiQuotaError("Gemini daily quota")

            def groq_cleaner(**_kwargs):
                error = GroqQuotaError(
                    "TPD limit reached; try again in 7m49.152s"
                )
                error.paused_at = "text_chunk_0002_subchunk_001"
                raise error

            result = clean_text_with_fallback(
                input_file=input_file,
                output_file=output_file,
                work_directory=work_directory,
                state_store=store,
                state=state,
                gemini_cleaner=gemini_cleaner,
                groq_cleaner=groq_cleaner,
            )

            reloaded = store.load()
            self.assertEqual(result["status"], "paused_quota")
            self.assertEqual(reloaded.status, "paused_quota")
            self.assertEqual(reloaded.paused_provider, "groq")
            self.assertEqual(
                reloaded.paused_at,
                "text_chunk_0002_subchunk_001",
            )
            self.assertIsNotNone(reloaded.resume_after)
            self.assertNotIn("7m49.152s", reloaded.last_error_message)
            self.assertEqual(
                output_file.read_text(encoding="utf-8"),
                "Birinci.\n\nİkinci.",
            )


if __name__ == "__main__":
    unittest.main()