import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DocumentStackContractTests(unittest.TestCase):
    def test_stack_surfaces_exist(self):
        required = [
            "docs/DOCUMENT_STACK.md",
            "requirements-analysis.txt",
            "requirements-windows.txt",
            "toolchain.toml",
            "scripts/activate_document_stack.ps1",
            "scripts/bootstrap_document_stack.ps1",
            "scripts/check_document_stack.py",
            "scripts/check_artifact_tool.mjs",
            "scripts/render_validate.py",
        ]
        for relative in required:
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_committed_stack_files_have_no_private_workspace_paths(self):
        files = [
            ROOT / "docs/DOCUMENT_STACK.md",
            ROOT / "scripts/activate_document_stack.ps1",
            ROOT / "scripts/bootstrap_document_stack.ps1",
            ROOT / "scripts/check_document_stack.py",
            ROOT / "scripts/check_artifact_tool.mjs",
            ROOT / "scripts/render_validate.py",
            ROOT / "tests/test_document_stack_contract.py",
        ]
        path_patterns = (r"[A-Za-z]:\\Users\\[^\\\s]+", r"[A-Za-z]:\\\.101_[^\\\s]+")
        private_tokens = ("Sub_" + "005", "CAL-" + "0020")
        for path in files:
            text = path.read_text(encoding="utf-8")
            for pattern in path_patterns:
                self.assertIsNone(re.search(pattern, text), f"private path pattern leaked into {path.name}")
            for token in private_tokens:
                self.assertNotIn(token, text, f"private token leaked into {path.name}")

    def test_artifact_probe_is_machine_readable(self):
        source = (ROOT / "scripts/check_artifact_tool.mjs").read_text(encoding="utf-8")
        self.assertIn("JSON.stringify", source)
        self.assertIn("createRequire", source)
        self.assertNotIn("import { SpreadsheetFile }", source)

    def test_core_profile_passes_in_ci_environment(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_document_stack.py"), "--profile", "core", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "PASS")

    def test_missing_native_capability_never_reports_pass(self):
        environment = {"PATH": "", "PYTHONPATH": ""}
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_document_stack.py"), "--profile", "render", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=environment,
        )
        self.assertIn(completed.returncode, (1, 2))
        payload = json.loads(completed.stdout)
        self.assertNotEqual(payload["status"], "PASS")

    def test_executable_identity_rejects_zero_exit_impostors(self):
        environment = dict(os.environ)
        environment.update(
            {
                "SYN_STUDIOS_NODE": sys.executable,
                "SYN_STUDIOS_PNPM": sys.executable,
                "SYN_STUDIOS_GIT": sys.executable,
            }
        )
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_document_stack.py"), "--profile", "dev", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=environment,
        )
        self.assertEqual(completed.returncode, 1)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "INCOMPATIBLE")
        self.assertTrue(all(item["status"] == "INCOMPATIBLE" for item in payload["executables"].values()))

    def test_repository_does_not_vendor_toolchain_binaries(self):
        forbidden_suffixes = {".exe", ".dll", ".msi", ".node", ".wasm", ".whl", ".so", ".dylib"}
        found = [
            str(path.relative_to(ROOT))
            for path in ROOT.rglob("*")
            if path.is_file()
            and ".git" not in path.parts
            and ".venv" not in path.parts
            and ".tooling" not in path.parts
            and (path.suffix.lower() in forbidden_suffixes or "node_modules" in path.parts)
        ]
        self.assertEqual(found, [])

    def test_dirty_render_output_is_rejected_before_tool_use(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            source = temp / "input.pdf"
            source.write_bytes(b"synthetic smoke input")
            output = temp / "output"
            output.mkdir()
            (output / "existing.txt").write_text("do not overwrite", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/render_validate.py"),
                    "--input",
                    str(source),
                    "--output-dir",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("must be empty", completed.stderr)
            self.assertEqual((output / "existing.txt").read_text(encoding="utf-8"), "do not overwrite")


if __name__ == "__main__":
    unittest.main()
