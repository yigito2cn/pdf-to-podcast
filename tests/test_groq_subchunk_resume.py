import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.groq_text_cleaner import (
    GroqQuotaError,
    clean_source_chunk_with_subchunks,
    validate_cleaned_text,
)


class GroqSubchunkResumeTests(unittest.TestCase):
    def test_completed_subchunk_is_reused_after_quota(self) -> None:
        source = ("A" * 600) + "\n\n" + ("B" * 600)

        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_directory = Path(temporary_directory) / "subchunks"

            def first_call(**kwargs):
                text = kwargs["chunk_text"]
                return text, validate_cleaned_text(text, text)

            with patch(
                "pipeline.groq_text_cleaner.clean_single_chunk",
                side_effect=[
                    first_call(chunk_text="A" * 600),
                    GroqQuotaError("TPD limit reached"),
                ],
            ):
                with self.assertRaises(GroqQuotaError) as raised:
                    clean_source_chunk_with_subchunks(
                        client=object(),
                        model_name="test-model",
                        source_chunk=source,
                        source_chunk_number=7,
                        total_source_chunks=10,
                        max_characters=700,
                        max_attempts=1,
                        request_interval_seconds=0,
                        subchunk_cache_directory=cache_directory,
                    )

            self.assertEqual(
                raised.exception.paused_at,
                "text_chunk_0007_subchunk_002",
            )

            first_cache = (
                cache_directory / "chunk_0007_subchunk_001.txt"
            )
            self.assertTrue(first_cache.is_file())

            calls = []

            def resumed_call(**kwargs):
                calls.append(kwargs["chunk_text"])
                text = kwargs["chunk_text"]
                return text, validate_cleaned_text(text, text)

            with patch(
                "pipeline.groq_text_cleaner.clean_single_chunk",
                side_effect=resumed_call,
            ):
                cleaned, validation, entries = (
                    clean_source_chunk_with_subchunks(
                        client=object(),
                        model_name="test-model",
                        source_chunk=source,
                        source_chunk_number=7,
                        total_source_chunks=10,
                        max_characters=700,
                        max_attempts=1,
                        request_interval_seconds=0,
                        subchunk_cache_directory=cache_directory,
                    )
                )

            self.assertEqual(calls, ["B" * 600])
            self.assertEqual(cleaned, source)
            self.assertTrue(validation["valid"])
            self.assertEqual(len(entries), 2)


if __name__ == "__main__":
    unittest.main()