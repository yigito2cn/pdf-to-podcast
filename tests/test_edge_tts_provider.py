import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.edge_tts_provider import (
    EdgeTTSPartialError,
    generate_edge_tts,
    merge_mp3_files,
    select_text_files,
)


class FakeCommunicator:
    calls = 0

    def __init__(self, text: str, voice: str) -> None:
        self.text = text
        self.voice = voice
        FakeCommunicator.calls += 1

    async def save(self, output_file: str) -> None:
        Path(output_file).write_bytes(b"ID3" + b"\x00" * 200)


class EdgeTtsProviderTests(unittest.TestCase):
    def test_chunk_failure_merges_valid_cached_audio_as_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_directory = root / "chunks"
            output_directory = root / "audio"
            input_directory.mkdir()
            for index in range(1, 3):
                (input_directory / f"tts_chunk_{index:04d}.txt").write_text(
                    f"Türkçe parça {index}", encoding="utf-8"
                )

            class FailingCommunicator(FakeCommunicator):
                async def save(self, output_file: str) -> None:
                    if "0002" in output_file:
                        raise RuntimeError("No audio")
                    await super().save(output_file)

            def fake_merger(files, output_file):
                output_file.write_bytes(
                    b"".join(path.read_bytes() for path in files)
                )

            with self.assertRaises(EdgeTTSPartialError) as raised:
                generate_edge_tts(
                    input_directory=input_directory,
                    output_directory=output_directory,
                    final_file=root / "podcast_edge.mp3",
                    communicate_factory=FailingCommunicator,
                    merger=fake_merger,
                )

            self.assertEqual(raised.exception.completed_chunks, 1)
            self.assertTrue(raised.exception.partial_file.is_file())

    def test_merge_uses_an_mp3_temporary_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_file = root / "chunk.mp3"
            source_file.write_bytes(b"ID3" + b"\x00" * 200)
            final_file = root / "podcast.mp3"

            with patch(
                "pipeline.edge_tts_provider.subprocess.run"
            ) as run_mock:
                run_mock.return_value.returncode = 0

                def create_output(command, **_kwargs):
                    Path(command[-1]).write_bytes(b"ID3" + b"\x00" * 200)
                    return run_mock.return_value

                run_mock.side_effect = create_output
                merge_mp3_files([source_file], final_file)

            temporary_path = Path(run_mock.call_args.args[0][-1])
            self.assertTrue(temporary_path.name.endswith(".tmp.mp3"))
            self.assertTrue(final_file.is_file())

    def test_daily_target_selects_only_enough_missing_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_directory = root / "chunks"
            output_directory = root / "audio"
            input_directory.mkdir()
            output_directory.mkdir()
            for index in range(1, 5):
                (input_directory / f"tts_chunk_{index:04d}.txt").write_text(
                    "bir iki üç dört beş", encoding="utf-8"
                )

            selected = select_text_files(
                input_directory=input_directory,
                output_directory=output_directory,
                target_minutes=0.05,
                words_per_minute=100,
            )

            self.assertEqual(
                [path.name for path in selected],
                ["tts_chunk_0001.txt"],
            )

    def test_edge_chunks_resume_from_source_hash_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_directory = root / "chunks"
            output_directory = root / "audio"
            input_directory.mkdir()
            for index in range(1, 3):
                (input_directory / f"tts_chunk_{index:04d}.txt").write_text(
                    f"Türkçe parça {index}", encoding="utf-8"
                )

            def fake_merger(files, output_file):
                output_file.write_bytes(b"".join(path.read_bytes() for path in files))

            FakeCommunicator.calls = 0
            final_file = root / "podcast.mp3"
            first = generate_edge_tts(
                input_directory=input_directory,
                output_directory=output_directory,
                final_file=final_file,
                communicate_factory=FakeCommunicator,
                merger=fake_merger,
            )
            second = generate_edge_tts(
                input_directory=input_directory,
                output_directory=output_directory,
                final_file=final_file,
                communicate_factory=FakeCommunicator,
                merger=fake_merger,
            )

            self.assertEqual(FakeCommunicator.calls, 2)
            self.assertEqual(first["completed_chunks"], 2)
            self.assertEqual(second["completed_chunks"], 2)
            self.assertEqual(first["source_total_chunks"], 2)
            self.assertGreater(final_file.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()