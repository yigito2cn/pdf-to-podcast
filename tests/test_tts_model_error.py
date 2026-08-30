import unittest
from unittest.mock import Mock

from batch_gemini_tts import GeminiTTSModelError, generate_audio


class TtsModelErrorTests(unittest.TestCase):
    def test_model_not_found_fails_without_retrying_other_chunks(self) -> None:
        client = Mock()
        client.models.generate_content.side_effect = RuntimeError(
            "404 NOT_FOUND model is not found"
        )

        with self.assertRaisesRegex(
            GeminiTTSModelError,
            "GEMINI_TTS_MODEL_NOT_FOUND",
        ):
            generate_audio(
                client=client,
                source_text="Türkçe metin",
                output_file=Mock(),
                model="invalid-model",
                voice_name="Kore",
                max_retries=6,
            )

        self.assertEqual(client.models.generate_content.call_count, 1)


if __name__ == "__main__":
    unittest.main()