import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.generate_workbook_recalculation_proof import (
    ProofGenerationFailed,
    main,
    publish_proof_once,
)


class RecalculationProofImmutabilityTests(unittest.TestCase):
    def test_initial_creation_publishes_generated_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence" / "workbook-recalculation.json"

            disposition = publish_proof_once(output, b"first proof\n")

            self.assertEqual(disposition, "written")
            self.assertEqual(output.read_bytes(), b"first proof\n")

    def test_byte_identical_rerun_does_not_touch_existing_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "workbook-recalculation.json"
            output.write_bytes(b"frozen proof\n")
            before = output.stat()

            with patch(
                "scripts.generate_workbook_recalculation_proof.tempfile.NamedTemporaryFile",
                side_effect=AssertionError("identical rerun must not stage replacement bytes"),
            ):
                disposition = publish_proof_once(output, b"frozen proof\n")

            after = output.stat()
            self.assertEqual(disposition, "unchanged")
            self.assertEqual(output.read_bytes(), b"frozen proof\n")
            self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(after.st_size, before.st_size)

    def test_changed_output_is_refused_before_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "evidence" / "workbook-recalculation.json"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"released proof\n")

            with (
                patch(
                    "scripts.generate_workbook_recalculation_proof.find_soffice",
                    return_value=Path("soffice-fixture"),
                ),
                patch(
                    "scripts.generate_workbook_recalculation_proof.generate",
                    return_value=(output, b"different proof\n"),
                ),
                patch(
                    "scripts.generate_workbook_recalculation_proof.tempfile.NamedTemporaryFile",
                    side_effect=AssertionError("changed proof must be refused before staging"),
                ),
            ):
                exit_code = main(["--root", str(root), "--write"])

            self.assertEqual(exit_code, 1)
            self.assertEqual(output.read_bytes(), b"released proof\n")


if __name__ == "__main__":
    unittest.main()
