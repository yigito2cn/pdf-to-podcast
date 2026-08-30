import tempfile
import unittest
from pathlib import Path

from batch_gemini_tts import checkpoint_tts_state
from pipeline.job_state import JobState, JobStateStore


class TtsJobStateTests(unittest.TestCase):
    def test_quota_checkpoint_is_not_overwritten_by_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = JobStateStore(Path(temporary_directory))
            state = JobState(
                job_id="job-1",
                pdf_file="book.pdf",
                pdf_sha256="abc123",
                start_page=1,
                end_page=2,
            )
            store.save(state)

            checkpoint_tts_state(
                state_store=store,
                state=state,
                status="tts_generating",
                total_chunks=307,
                completed_chunks=14,
            )
            checkpoint_tts_state(
                state_store=store,
                state=state,
                status="paused_quota",
                total_chunks=307,
                completed_chunks=14,
                paused_at="tts_chunk_0015",
                error_message="daily quota",
                resume_after="2026-08-31T00:00:00+00:00",
            )

            reloaded = store.load()
            self.assertEqual(reloaded.status, "paused_quota")
            self.assertEqual(reloaded.stage, "tts_generating")
            self.assertEqual(reloaded.paused_at, "tts_chunk_0015")
            self.assertEqual(reloaded.tts_chunks_completed, 14)
            self.assertEqual(reloaded.tts_chunks_total, 307)
            self.assertEqual(
                reloaded.resume_after,
                "2026-08-31T00:00:00+00:00",
            )


if __name__ == "__main__":
    unittest.main()