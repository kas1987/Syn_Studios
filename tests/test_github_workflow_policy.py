import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
ACTION_PIN = re.compile(r"^\s*-?\s*uses:\s*[^@\s]+@([0-9a-f]{40})(?:\s+#.*)?$", re.MULTILINE)
ANY_ACTION = re.compile(r"^\s*-?\s*uses:\s*[^@\s]+@([^\s#]+)", re.MULTILINE)


class GitHubWorkflowPolicyTests(unittest.TestCase):
    def workflow(self, name: str) -> str:
        return (WORKFLOWS / name).read_text(encoding="utf-8")

    def test_every_action_is_pinned_to_a_full_commit(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            references = ANY_ACTION.findall(text)
            self.assertTrue(references, path.name)
            self.assertEqual(len(references), len(ACTION_PIN.findall(text)), path.name)

    def test_workflows_exclude_known_bypass_controls(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("pull_request_target:", text, path.name)
            self.assertNotIn("continue-on-error:", text, path.name)
            self.assertIn("timeout-minutes:", text, path.name)

    def test_protected_contract_aggregates_both_matrix_legs(self):
        text = self.workflow("validate.yml")
        self.assertIn("os: [ubuntu-latest, windows-latest]", text)
        self.assertIn("protected-contract:\n    name: contract\n    needs: tests", text)
        self.assertIn("if: always()", text)
        self.assertIn('run: test "$TEST_RESULT" = success', text)
        self.assertIn("permissions:\n  contents: read", text)

    def test_security_and_dependency_workflows_are_read_scoped(self):
        security = self.workflow("security.yml")
        dependency = self.workflow("dependency-review.yml")
        self.assertIn("contents: read", security)
        self.assertIn("actions: read", security)
        self.assertIn("security-events: write", security)
        self.assertIn("permissions:\n  contents: read", dependency)


if __name__ == "__main__":
    unittest.main()
