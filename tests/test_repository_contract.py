import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryContractTests(unittest.TestCase):
    def test_required_sources_exist(self):
        for relative in ("SYNTHETIC_DESIGN.md", "skill/SKILL.md", "schemas/foundation-card.schema.json"):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_skill_has_required_frontmatter(self):
        text = (ROOT / "skill/SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nname: synthetic-studio\n"))
        self.assertIn("\ndescription:", text.split("---", 2)[1])

    def test_foundation_schema_is_json(self):
        data = json.loads((ROOT / "schemas/foundation-card.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(data["properties"]["card_id"]["pattern"], "^FOUND-[0-9]{4}$")

    def test_foundation_cards_have_identity_and_reuse_boundaries(self):
        cards = sorted((ROOT / "library/foundations").glob("FOUND-*.json"))
        self.assertGreaterEqual(len(cards), 1)
        seen = set()
        for path in cards:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertRegex(data["card_id"], r"^FOUND-[0-9]{4}$")
            self.assertNotIn(data["card_id"], seen)
            seen.add(data["card_id"])
            self.assertRegex(data["source"]["sha256"], r"^[a-f0-9]{64}$")
            self.assertTrue(data["source"]["synthetic_authorized"])
            self.assertGreater(len(data["reuse"]["patterns"]), 0)
            self.assertGreater(len(data["reuse"]["proof_gate"]), 0)

    def test_blueprints_have_unique_ids_and_finish_gates(self):
        blueprints = sorted((ROOT / "examples/blueprints").glob("BP-*.json"))
        self.assertGreaterEqual(len(blueprints), 5)
        seen = set()
        for path in blueprints:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertRegex(data["blueprint_id"], r"^BP-[0-9]{4}$")
            self.assertNotIn(data["blueprint_id"], seen)
            seen.add(data["blueprint_id"])
            self.assertGreaterEqual(len(data["complexity_layers"]), 2)
            self.assertGreater(len(data["prohibited"]), 0)
            self.assertGreater(len(data["proof_gates"]), 0)


if __name__ == "__main__":
    unittest.main()
