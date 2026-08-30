import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.immutable_publication import (
    PublicationSafetyError,
    open_staging_file,
    verify_parent,
)
from scripts.generate_workbook_recalculation_proof import (
    ProofGenerationFailed,
    main,
    publish_proof_once,
)


class RecalculationProofImmutabilityTests(unittest.TestCase):
    def test_parent_accepts_an_equivalent_nonlexical_root_spelling(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            actual_parent = root / "evidence"
            actual_parent.mkdir()
            alias_root = root.with_name(root.name + "~alias")
            alias_parent = alias_root / "evidence"
            target = alias_parent / "workbook-recalculation.json"
            real_resolve = Path.resolve
            real_stat = Path.stat
            real_samefile = os.path.samefile

            def resolve(path, *args, **kwargs):
                if path == root:
                    return root
                if path == alias_parent:
                    return actual_parent
                return real_resolve(path, *args, **kwargs)

            def path_stat(path, *args, **kwargs):
                if path == alias_parent:
                    return real_stat(actual_parent, *args, **kwargs)
                if path == alias_root:
                    return real_stat(root, *args, **kwargs)
                return real_stat(path, *args, **kwargs)

            def samefile(left, right):
                left_path = Path(left)
                right_path = Path(right)
                if left_path == alias_parent:
                    return False
                if left_path == alias_root and right_path == root:
                    return True
                return real_samefile(left, right)

            with (
                patch("pathlib.Path.resolve", autospec=True, side_effect=resolve),
                patch("pathlib.Path.stat", autospec=True, side_effect=path_stat),
                patch(
                    "scripts.immutable_publication.os.path.samefile",
                    side_effect=samefile,
                ),
            ):
                verify_parent(root, target)

    def test_parent_rejects_equivalent_root_reached_through_indirect_ancestor(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary).resolve()
            real_parent = temporary_root / "real"
            root = real_parent / "repository"
            evidence = root / "evidence"
            evidence.mkdir(parents=True)
            alias_parent = temporary_root / "alias"
            try:
                alias_parent.symlink_to(real_parent, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks are unavailable")
            target = alias_parent / "repository" / "evidence" / "proof.json"

            with self.assertRaisesRegex(PublicationSafetyError, "direct directories"):
                verify_parent(root, target)

    def test_initial_creation_publishes_generated_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence" / "workbook-recalculation.json"

            disposition = publish_proof_once(
                output, b"first proof\n", root=Path(temporary)
            )

            self.assertEqual(disposition, "written")
            self.assertEqual(output.read_bytes(), b"first proof\n")

    def test_byte_identical_rerun_does_not_touch_existing_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "workbook-recalculation.json"
            output.write_bytes(b"frozen proof\n")
            before = output.stat()

            with patch(
                "scripts.generate_workbook_recalculation_proof.open_staging_file",
                side_effect=AssertionError("identical rerun must not stage replacement bytes"),
            ):
                disposition = publish_proof_once(
                    output, b"frozen proof\n", root=Path(temporary)
                )

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
                    "scripts.generate_workbook_recalculation_proof.open_staging_file",
                    side_effect=AssertionError("changed proof must be refused before staging"),
                ),
            ):
                exit_code = main(["--root", str(root), "--write"])

            self.assertEqual(exit_code, 1)
            self.assertEqual(output.read_bytes(), b"released proof\n")

    def test_byte_identical_preexisting_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "repo"
            output = root / "evidence" / "workbook-recalculation.json"
            output.parent.mkdir(parents=True)
            outside = base / "outside-proof.json"
            outside.write_bytes(b"frozen proof\n")
            try:
                output.symlink_to(outside)
            except OSError as error:
                self.skipTest(f"file symlinks are unavailable: {error}")

            with self.assertRaisesRegex(ProofGenerationFailed, "direct regular file"):
                publish_proof_once(output, b"frozen proof\n", root=root)

            self.assertTrue(output.is_symlink())
            self.assertEqual(outside.read_bytes(), b"frozen proof\n")

    def test_concurrent_byte_identical_symlink_is_rejected_without_clobber(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "repo"
            root.mkdir()
            output = root / "evidence" / "workbook-recalculation.json"
            outside = base / "outside-proof.json"

            def symlink_target_appears(source, target, **kwargs):
                outside.write_bytes(Path(source).read_bytes())
                target = Path(target)
                if not target.is_absolute():
                    target = output.parent / target
                try:
                    target.symlink_to(outside)
                except OSError as error:
                    self.skipTest(f"file symlinks are unavailable: {error}")
                raise FileExistsError("injected identical symlink target")

            with (
                patch(
                    "scripts.immutable_publication.os.link",
                    side_effect=symlink_target_appears,
                ),
                self.assertRaisesRegex(ProofGenerationFailed, "direct regular file"),
            ):
                publish_proof_once(output, b"frozen proof\n", root=root)

            self.assertTrue(output.is_symlink())
            self.assertEqual(output.resolve(), outside.resolve())

    def test_final_verification_failure_leaves_complete_recoverable_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence" / "workbook-recalculation.json"

            with (
                patch(
                    "scripts.generate_workbook_recalculation_proof.read_stable_proof",
                    side_effect=[None, b"unexpected proof\n"],
                ),
                self.assertRaisesRegex(ProofGenerationFailed, "final publication"),
            ):
                publish_proof_once(
                    output, b"frozen proof\n", root=Path(temporary)
                )

            self.assertEqual(output.read_bytes(), b"frozen proof\n")
            self.assertEqual(
                publish_proof_once(output, b"frozen proof\n", root=Path(temporary)),
                "unchanged",
            )

    def test_final_verification_preserves_concurrent_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence" / "workbook-recalculation.json"
            inspections = 0

            def replace_during_final_verification(
                path, root=None, directory_descriptor=None
            ):
                nonlocal inspections
                inspections += 1
                if inspections == 1:
                    return None
                path.unlink()
                path.write_bytes(b"concurrent proof\n")
                return b"concurrent proof\n"

            with (
                patch(
                    "scripts.generate_workbook_recalculation_proof.read_stable_proof",
                    side_effect=replace_during_final_verification,
                ),
                self.assertRaisesRegex(ProofGenerationFailed, "final publication"),
            ):
                publish_proof_once(
                    output, b"frozen proof\n", root=Path(temporary)
                )

            self.assertEqual(output.read_bytes(), b"concurrent proof\n")

    def test_link_then_keyboard_interrupt_leaves_complete_recoverable_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "evidence" / "workbook-recalculation.json"
            real_link = os.link

            def link_then_interrupt(source, target, **kwargs):
                real_link(source, target, **kwargs)
                raise KeyboardInterrupt("injected cancellation after hard link")

            with (
                patch(
                    "scripts.immutable_publication.os.link",
                    side_effect=link_then_interrupt,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                publish_proof_once(output, b"frozen proof\n", root=root)

            self.assertEqual(output.read_bytes(), b"frozen proof\n")
            self.assertEqual(list(root.glob("*.tmp")), [])
            self.assertEqual(
                publish_proof_once(output, b"frozen proof\n", root=root),
                "unchanged",
            )

    def test_staging_path_replacement_cannot_publish_replacement_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "evidence" / "workbook-recalculation.json"
            replacement = b"attacker replacement bytes\n"
            real_link = os.link
            replacement_path: Path | None = None

            def replace_source_before_link(source, target, **kwargs):
                nonlocal replacement_path
                if os.name == "nt":
                    replacement_path = Path(source)
                else:
                    replacement_path = Path(os.readlink(source))
                replacement_path.unlink()
                replacement_path.write_bytes(replacement)
                return real_link(source, target, **kwargs)

            with (
                patch(
                    "scripts.immutable_publication.os.link",
                    side_effect=replace_source_before_link,
                ),
                self.assertRaisesRegex(
                    ProofGenerationFailed,
                    "publication failed",
                ),
            ):
                publish_proof_once(output, b"frozen proof\n", root=root)

            self.assertFalse(output.exists())
            if os.name == "nt":
                self.assertIsNotNone(replacement_path)
                self.assertFalse(replacement_path.exists())
                self.assertEqual(list(root.glob("*.tmp")), [])
            else:
                self.assertIsNotNone(replacement_path)
                self.assertEqual(replacement_path.read_bytes(), replacement)

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics")
    def test_in_place_staging_writer_is_denied_before_proof_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "evidence" / "workbook-recalculation.json"
            real_link = os.link

            def mutate_source_before_link(source, target, **kwargs):
                with Path(source).open("r+b") as attacker:
                    attacker.seek(0)
                    attacker.write(b"attacker replacement bytes\n")
                    attacker.truncate()
                return real_link(source, target, **kwargs)

            with (
                patch(
                    "scripts.immutable_publication.os.link",
                    side_effect=mutate_source_before_link,
                ),
                self.assertRaisesRegex(
                    ProofGenerationFailed,
                    "publication failed",
                ),
            ):
                publish_proof_once(output, b"frozen proof\n", root=root)

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_publication_safety_error_uses_proof_refusal_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "evidence" / "workbook-recalculation.json"

            with (
                patch(
                    "scripts.generate_workbook_recalculation_proof.hard_link",
                    side_effect=PublicationSafetyError("injected safety failure"),
                ),
                self.assertRaisesRegex(
                    ProofGenerationFailed,
                    "publication failed: injected safety failure",
                ),
            ):
                publish_proof_once(output, b"frozen proof\n", root=root)

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_fsync_failure_cleans_staging_without_publishing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "evidence" / "workbook-recalculation.json"

            with (
                patch(
                    "scripts.generate_workbook_recalculation_proof.os.fsync",
                    side_effect=OSError("injected fsync failure"),
                ),
                self.assertRaisesRegex(OSError, "fsync failure"),
            ):
                publish_proof_once(output, b"frozen proof\n", root=root)

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_root_replacement_during_staging_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "repo"
            root.mkdir()
            moved_root = base / "moved-repo"
            output = root / "evidence" / "workbook-recalculation.json"
            real_stage = open_staging_file

            def replace_root_before_stage(*args, **kwargs):
                root.rename(moved_root)
                root.mkdir()
                return real_stage(*args, **kwargs)

            with (
                patch(
                    "scripts.generate_workbook_recalculation_proof.open_staging_file",
                    side_effect=replace_root_before_stage,
                ),
                self.assertRaisesRegex(ProofGenerationFailed, "repository root changed"),
            ):
                publish_proof_once(output, b"frozen proof\n", root=root)

            self.assertFalse((moved_root / "evidence/workbook-recalculation.json").exists())
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_parent_swap_during_staging_never_writes_outside_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "repo"
            root.mkdir()
            outside = base / "outside-proofs"
            outside.mkdir()
            proof_directory = root / "evidence"
            output = proof_directory / "workbook-recalculation.json"
            real_stage = open_staging_file

            def swap_parent_before_stage(*args, **kwargs):
                proof_directory.rmdir()
                try:
                    proof_directory.symlink_to(outside, target_is_directory=True)
                except OSError as error:
                    self.skipTest(f"directory symlinks are unavailable: {error}")
                return real_stage(*args, **kwargs)

            with (
                patch(
                    "scripts.generate_workbook_recalculation_proof.open_staging_file",
                    side_effect=swap_parent_before_stage,
                ),
                self.assertRaisesRegex(ProofGenerationFailed, "repository root"),
            ):
                publish_proof_once(output, b"frozen proof\n", root=root)

            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_out_of_root_proof_directory_is_rejected_before_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "repo"
            root.mkdir()
            outside = base / "outside-proofs"
            outside.mkdir()
            proof_directory = root / "evidence"
            try:
                proof_directory.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"directory symlinks are unavailable: {error}")
            output = proof_directory / "workbook-recalculation.json"

            with (
                patch(
                    "scripts.generate_workbook_recalculation_proof.open_staging_file",
                    wraps=open_staging_file,
                ) as stage,
                self.assertRaisesRegex(ProofGenerationFailed, "repository root"),
            ):
                publish_proof_once(output, b"frozen proof\n", root=root)

            stage.assert_not_called()
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
