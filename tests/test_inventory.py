import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.inventory_artifacts import inspect_csv, iter_targets, sha256_bytes


class InventoryTests(unittest.TestCase):
    def test_csv_metrics(self):
        result = inspect_csv(b"a,b\n1,2\n,\n")
        self.assertEqual(result["rows"], 3)
        self.assertEqual(result["columns_max"], 2)
        self.assertEqual(result["blank_rows"], 1)

    def test_hash_is_deterministic(self):
        self.assertEqual(sha256_bytes(b"same"), sha256_bytes(b"same"))
        self.assertNotEqual(sha256_bytes(b"same"), sha256_bytes(b"different"))

    def test_zip_inventory_preserves_container_and_member(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("inputs/sample.csv", "a,b\n1,2\n")
            records = list(iter_targets([path]))
            self.assertEqual(records[0]["type"], "zip")
            self.assertEqual(records[1]["container"], str(path))
            self.assertEqual(records[1]["detail"]["rows"], 2)

    def test_inventory_records_are_json_serializable(self):
        rendered = json.dumps(list(iter_targets([])))
        self.assertEqual(rendered, "[]")


if __name__ == "__main__":
    unittest.main()
