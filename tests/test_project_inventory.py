import tempfile
import unittest
from pathlib import Path

from scripts.project_inventory import build_inventory


class ProjectInventoryTests(unittest.TestCase):
    def test_inventory_excludes_secrets_and_classifies_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".env").write_text("SECRET=value", encoding="utf-8")
            (root / "source.py").write_text("pass", encoding="utf-8")
            output_directory = root / "work" / "job"
            output_directory.mkdir(parents=True)
            (output_directory / "chunk.txt").write_text("data", encoding="utf-8")

            inventory = build_inventory(root)
            paths = {entry["path"] for entry in inventory["files"]}

            self.assertNotIn(".env", paths)
            self.assertIn("source.py", paths)
            self.assertIn("work/job/chunk.txt", paths)
            categories = {
                entry["path"]: entry["category"]
                for entry in inventory["files"]
            }
            self.assertEqual(categories["source.py"], "source")
            self.assertEqual(
                categories["work/job/chunk.txt"],
                "production_or_intermediate_data",
            )


if __name__ == "__main__":
    unittest.main()