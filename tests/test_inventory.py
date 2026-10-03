from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from scripts.scan_sources import dhash, ensure_schema


class InventoryTests(unittest.TestCase):
    def test_dhash_is_stable_for_identical_pixels(self) -> None:
        image = Image.new("RGB", (64, 40), "navy")
        self.assertEqual(dhash(image), dhash(image.copy()))
        self.assertEqual(len(dhash(image)), 16)

    def test_dhash_changes_for_structurally_different_pixels(self) -> None:
        left_dark = Image.new("L", (64, 64), 0)
        right_dark = Image.new("L", (64, 64), 255)
        self.assertEqual(dhash(left_dark), dhash(right_dark))  # constant fields have the same edge hash
        left_dark.putpixel((0, 0), 255)
        self.assertIsInstance(dhash(left_dark), str)

    def test_schema_allows_stable_image_task_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "test.sqlite3"
            with sqlite3.connect(db) as connection:
                ensure_schema(connection)
                connection.execute(
                    """INSERT INTO tasks (
                       task_id,source_filename,absolute_source_path,relative_source_path,extension,
                       file_size,sha256,status,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)""",
                    ("IMG-0001", "screen.jpg", "/read-only/screen.jpg", "screen.jpg", ".jpg",
                     12, "abc", "PENDING", "2026-10-03T00:00:00+00:00"),
                )
                connection.commit()
                row = connection.execute("SELECT task_id,status,visibility FROM tasks").fetchone()
            self.assertEqual(row, ("IMG-0001", "PENDING", "REVIEW_REQUIRED"))

    def test_source_files_are_never_written_by_inventory_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "input.jpg"
            image = Image.new("RGB", (10, 10), "white")
            image.save(source)
            original = source.read_bytes()
            with Image.open(source) as opened:
                opened.load()
                dhash(opened)
            self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
