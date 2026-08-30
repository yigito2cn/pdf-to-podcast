import json
import tempfile
import unittest
from pathlib import Path

from pipeline.job_state import JobState, JobStateStore, create_job_id


class JobStateStoreTests(unittest.TestCase):
    def test_job_id_is_stable_and_page_range_specific(self) -> None:
        first = create_job_id("abc123", 8, 334)
        repeated = create_job_id("abc123", 8, 334)
        different_range = create_job_id("abc123", 9, 334)

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, different_range)

    def test_quota_checkpoint_survives_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            store = JobStateStore(work_directory)
            state = JobState(
                job_id="job-1",
                pdf_file="book.pdf",
                pdf_sha256="abc123",
                start_page=8,
                end_page=334,
                text_chunks_total=91,
                text_chunks_completed=69,
            )

            store.save(state)
            store.pause_for_quota(
                state,
                stage="text_cleaning",
                provider="groq",
                paused_at="text_chunk_0070_subchunk_02",
                resume_after="2026-08-31T00:00:00+00:00",
                error_message="TPD limit reached",
            )

            reloaded = store.load()

            self.assertEqual(reloaded.status, "paused_quota")
            self.assertEqual(reloaded.paused_provider, "groq")
            self.assertEqual(
                reloaded.paused_at,
                "text_chunk_0070_subchunk_02",
            )
            self.assertEqual(reloaded.text_chunks_completed, 69)
            self.assertEqual(reloaded.last_error_type, "daily_quota")
            self.assertFalse(
                store.status_file.with_suffix(".json.tmp").exists()
            )

            raw_state = json.loads(
                store.status_file.read_text(encoding="utf-8")
            )
            self.assertEqual(raw_state["status"], "paused_quota")

    def test_load_or_create_preserves_existing_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = JobStateStore(Path(temporary_directory))
            original = JobState(
                job_id="job-1",
                pdf_file="book.pdf",
                pdf_sha256="abc123",
                start_page=1,
                end_page=3,
                status="text_cleaning",
                text_chunks_completed=2,
            )
            store.save(original)

            replacement = JobState(
                job_id="job-1",
                pdf_file="book.pdf",
                pdf_sha256="abc123",
                start_page=1,
                end_page=3,
            )
            loaded = store.load_or_create(replacement)

            self.assertEqual(loaded.status, "text_cleaning")
            self.assertEqual(loaded.text_chunks_completed, 2)


if __name__ == "__main__":
    unittest.main()