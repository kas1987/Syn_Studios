import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VersioningPolicyTests(unittest.TestCase):
    def test_lifecycle_surfaces_exist(self):
        for relative in (
            "CHANGELOG.md",
            "docs/VERSIONING.md",
            "docs/MIGRATIONS.md",
            "library/README.md",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_policy_requires_exact_immutable_versions(self):
        policy = (ROOT / "docs/VERSIONING.md").read_text(encoding="utf-8")
        self.assertIn("semantic versioning", policy)
        self.assertIn("released native asset is immutable", policy)
        self.assertIn("exact template version", policy)
        self.assertIn("floating `latest` references", policy)

    def test_lifecycle_distinguishes_deprecation_and_withdrawal(self):
        policy = (ROOT / "docs/VERSIONING.md").read_text(encoding="utf-8")
        self.assertIn("deprecated", policy)
        self.assertIn("withdrawn", policy)
        self.assertIn("must not be selected", policy)


if __name__ == "__main__":
    unittest.main()
