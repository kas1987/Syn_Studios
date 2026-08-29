import hashlib
import json
import re
import shutil
import struct
import tempfile
import unittest
import unicodedata
import zipfile
import zlib
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.validate_library import SCHEMA_NAMES, canonical_json_sha256, validate_native_asset_shape, validate_proof_artifact, validate_repository


ROOT = Path(__file__).resolve().parents[1]


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_render_pdf(path, pages=1):
    page_objects = b"\n".join(
        f"{index} 0 obj <</Type /Page /MediaBox [0 0 612 792] /Contents 99 0 R>> endobj".encode()
        for index in range(1, pages + 1)
    )
    payload = b"%PDF-1.7\n" + page_objects + b"\n99 0 obj <</Length 18>> stream\nBT (proof) Tj ET\nendstream endobj\n%%EOF\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def write_render_png(path, marker=0):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes([(row + marker) % 256] * 16) for row in range(16))
    payload = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


class LibraryControlPlaneTests(unittest.TestCase):
    def make_minimal_root(self, temporary):
        root = Path(temporary)
        for name in SCHEMA_NAMES:
            target = root / "schemas" / f"{name}.schema.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / "schemas" / target.name, target)
        card_root = root / "library/foundations"
        card_root.mkdir(parents=True, exist_ok=True)
        for source in (ROOT / "library/foundations").glob("FOUND-*.json"):
            shutil.copy2(source, card_root / source.name)
        card_target = card_root / "FOUND-0001.json"
        blueprint_root = root / "examples/blueprints"
        blueprint_root.mkdir(parents=True, exist_ok=True)
        for source in (ROOT / "examples/blueprints").glob("BP-*.json"):
            shutil.copy2(source, blueprint_root / source.name)
        blueprint_target = blueprint_root / "BP-0001.internal-close-workbook.json"
        fixture_root = root / "examples/blueprints/fixtures"
        fixture_root.mkdir(parents=True, exist_ok=True)
        for source in (ROOT / "examples/blueprints/fixtures").glob("*.json"):
            shutil.copy2(source, fixture_root / source.name)
        return root, card_target, blueprint_target

    def assert_finding(self, root, fragment):
        findings, _ = validate_repository(root)
        self.assertTrue(any(fragment in item for item in findings), "\n".join(findings))

    def make_release(self, root, blueprint_path):
        blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
        asset = root / "library/templates/TMPL-0001/1.0.0/workbook.xlsx"
        asset.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(asset, "w") as package:
            package.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>')
            package.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
            package.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
            package.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>')
        asset_binding = {"path": asset.relative_to(root).as_posix(), "sha256": file_hash(asset)}
        descriptor = {
            "schema_version": "1.0.0",
            "template_id": "TMPL-0001",
            "version": "1.0.0",
            "name": "Internal close and reconciliation workbook",
            "release_status": "released",
            "artifact_type": "xlsx",
            "blueprint_id": "BP-0001",
            "native_assets": [{**asset_binding, "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}],
            "producer": {"role": "senior accountant", "department": "finance"},
            "purpose": "Reconcile authorized source activity to the ledger.",
            "lifecycle": blueprint["lifecycle"],
            "authority": blueprint["authority"]["primary_class"],
            "slots": ["organization_name"],
            "slot_contract": {"token_format": "{{slot_name}}", "required": True, "instantiated_artifact_policy": "reject unresolved tokens", "value_source": "authorized world facts only"},
            "render_contract": {"required": True, "evidence_manifest": "evidence/template-releases/REL-0001/render-manifest.json", "expected_page_count": 1, "expected_sheet_names": ["Sheet1"], "expected_pdf_path": "evidence/template-releases/REL-0001/render.pdf", "expected_page_image_pattern": "evidence/template-releases/REL-0001/page-{page}.png", "print_policy": "Render the bounded worksheet as one readable page."},
            "knowledge_and_authority_constraints": ["Source rows cannot manufacture conclusions."],
            "prohibited_content": ["prior submission facts"],
            "supported_consumers": ["anna-holodeck-bridge"],
            "capabilities": ["recalculate", "render"],
            "generation_notes": ["Populate source layers before reconciliation."],
            "proof_expectations": [{"id": "render-all", "capability": "render", "required": True, "description": "Render and inspect every worksheet."}],
        }
        descriptor_path = asset.parent / "template.json"
        write_json(descriptor_path, descriptor)
        descriptor_binding = {"path": descriptor_path.relative_to(root).as_posix(), "sha256": file_hash(descriptor_path)}
        render_pdf = root / descriptor["render_contract"]["expected_pdf_path"]
        render_png = root / descriptor["render_contract"]["expected_page_image_pattern"].format(page=1)
        write_render_pdf(render_pdf)
        write_render_png(render_png)
        render_outputs = [
            {"path": render_pdf.relative_to(root).as_posix(), "sha256": file_hash(render_pdf)},
            {"path": render_png.relative_to(root).as_posix(), "sha256": file_hash(render_png)},
        ]
        write_json(root / descriptor["render_contract"]["evidence_manifest"], {
            "schema_version": "1.0.0",
            "templates": {"TMPL-0001": {
                "asset_path": asset_binding["path"], "asset_sha256": asset_binding["sha256"],
                "page_count": 1, "sheet_names": ["Sheet1"], "rendered_outputs": render_outputs,
            }},
        })

        def evidence(name, record_type, verdict, actor_id, actor, categories=None, procedures=None):
            artifact_categories = categories or ["provenance"]
            artifacts = []
            for category in artifact_categories:
                if category == "render":
                    artifacts.extend({**item, "media_type": "application/pdf" if index == 0 else "image/png", "category": "render"} for index, item in enumerate(render_outputs))
                    continue
                proof = root / "evidence/template-releases/REL-0001/proofs" / f"{name}-{category}.txt"
                proof.parent.mkdir(parents=True, exist_ok=True)
                proof.write_text(
                    f"release_id=REL-0001\ntemplate_id=TMPL-0001\ncategory={category}\n"
                    f"native_asset_sha256={asset_binding['sha256']}\nobservation=Validated {name} output.\n",
                    encoding="utf-8",
                )
                artifacts.append({"path": proof.relative_to(root).as_posix(), "sha256": file_hash(proof), "media_type": "text/plain", "category": category})
            record = {
                "schema_version": "1.0.0",
                "record_id": f"EVID-RECORD-{name.upper()}",
                "record_type": record_type,
                "release_id": "REL-0001",
                "template_id": "TMPL-0001",
                "version": "1.0.0",
                "descriptor_sha256": descriptor_binding["sha256"],
                "native_asset_sha256s": [asset_binding["sha256"]],
                "verdict": verdict,
                "actor_id": actor_id,
                "actor": actor,
                "observations": [f"Observed concrete output for the {name} gate."],
                "artifacts": artifacts,
                "summary": f"Typed evidence for the {name} release gate.",
            }
            if categories is not None:
                record["categories"] = categories
            if procedures is not None:
                record["procedures"] = procedures
            if categories is not None and "render" in categories:
                record["render_contract_sha256"] = canonical_json_sha256(descriptor["render_contract"])
            path = root / "evidence/template-releases/REL-0001" / f"{name}.json"
            write_json(path, record)
            return {"record_path": path.relative_to(root).as_posix(), "record_sha256": file_hash(path)}

        build = evidence("build", "build_attestation", "BUILD_COMPLETE", "builder:template", "Template builder")
        sanitization = evidence("sanitization", "sanitization", "SANITIZATION_PASS", "reviewer:sanitization", "Sanitization reviewer")
        terra = evidence("terra", "terra_review", "USABILITY_PASS", "reviewer:terra", "Terra reviewer")
        sol = evidence("sol", "sol_review", "INTEGRITY_PASS", "reviewer:sol", "Sol reviewer")
        conductor = evidence("conductor", "conductor_approval", "APPROVED", "reviewer:conductor", "Conductor")
        procedures = {gate["category"]: gate["procedure"] for gate in blueprint["proof_gates"]}
        technical = evidence("technical", "technical_validation", "VALIDATION_PASS", "runner:validation", "Validation runner", sorted(procedures), procedures)
        release = {
            "schema_version": "3.0.0",
            "release_id": "REL-0001",
            "template_id": "TMPL-0001",
            "version": "1.0.0",
            "status": "released",
            "descriptor": descriptor_binding,
            "native_assets": [asset_binding],
            "blueprint": {"blueprint_id": "BP-0001", "path": blueprint_path.relative_to(root).as_posix(), "sha256": file_hash(blueprint_path)},
            "build": build,
            "sanitization": {"evidence": sanitization, "foundation_card_ids": ["FOUND-0001"]},
            "reviews": {"terra": terra, "sol": sol},
            "conductor_approval": conductor,
            "evidence": {category: technical for category in sorted(g["category"] for g in blueprint["proof_gates"])},
        }
        release_path = root / "library/releases/REL-0001.template.json"
        write_json(release_path, release)
        return release_path, release, descriptor_path, descriptor

    def make_catalog(self, root, release_path, release, descriptor):
        blueprint = json.loads((root / release["blueprint"]["path"]).read_text(encoding="utf-8"))
        catalog = {
            "schema_version": "1.0.0",
            "catalog_id": "syn-studios-artifact-library",
            "discovery_fields": ["artifact_type", "authority", "lifecycle", "capabilities", "supported_consumers", "blueprint_id", "release_status"],
            "templates": [{
                "kind": "artifact_template",
                "template_id": release["template_id"],
                "version": release["version"],
                "name": descriptor["name"],
                "artifact_type": descriptor["artifact_type"],
                "blueprint_id": release["blueprint"]["blueprint_id"],
                "authority": blueprint["authority"]["primary_class"],
                "lifecycle": blueprint["lifecycle"],
                "descriptor": release["descriptor"]["path"],
                "native_assets": [item["path"] for item in release["native_assets"]],
                "supported_consumers": descriptor["supported_consumers"],
                "capabilities": descriptor["capabilities"],
                "release_status": "released",
                "release_record": {"path": release_path.relative_to(root).as_posix(), "sha256": file_hash(release_path)},
            }],
        }
        path = root / "library/catalog.json"
        write_json(path, catalog)
        return path, catalog

    def rebind_native_asset(self, root, release_path, release, descriptor_path, descriptor):
        asset_path = root / release["native_assets"][0]["path"]
        asset_hash = file_hash(asset_path)
        release["native_assets"][0]["sha256"] = asset_hash
        descriptor["native_assets"][0]["sha256"] = asset_hash
        write_json(descriptor_path, descriptor)
        descriptor_hash = file_hash(descriptor_path)
        release["descriptor"]["sha256"] = descriptor_hash
        manifest_path = root / descriptor["render_contract"]["evidence_manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["templates"][descriptor["template_id"]]["asset_sha256"] = asset_hash
        write_json(manifest_path, manifest)
        evidence_references = [release["build"], release["sanitization"]["evidence"], release["reviews"]["terra"], release["reviews"]["sol"], release["conductor_approval"], *release["evidence"].values()]
        seen_paths = set()
        for reference in evidence_references:
            evidence_path = root / reference["record_path"]
            if evidence_path in seen_paths:
                continue
            seen_paths.add(evidence_path)
            record = json.loads(evidence_path.read_text(encoding="utf-8"))
            record["descriptor_sha256"] = descriptor_hash
            record["native_asset_sha256s"] = [asset_hash]
            for artifact in record["artifacts"]:
                proof_path = root / artifact["path"]
                if artifact.get("media_type", "").startswith("text/"):
                    proof_text = proof_path.read_text(encoding="utf-8")
                    proof_text = re.sub(r"native_asset_sha256=[a-f0-9]{64}", f"native_asset_sha256={asset_hash}", proof_text)
                    proof_path.write_text(proof_text, encoding="utf-8")
                artifact["sha256"] = file_hash(proof_path)
            write_json(evidence_path, record)
        for reference in evidence_references:
            reference["record_sha256"] = file_hash(root / reference["record_path"])
        write_json(release_path, release)

    def test_updated_repository_records_and_fixtures_pass(self):
        findings, count = validate_repository(ROOT)
        self.assertEqual(findings, [])
        self.assertGreaterEqual(count, 35)

    def test_foundation_cannot_self_promote_to_template_ready(self):
        schema = json.loads((ROOT / "schemas/foundation-card.schema.json").read_text(encoding="utf-8"))
        card = json.loads((ROOT / "library/foundations/FOUND-0001.json").read_text(encoding="utf-8"))
        card["status"] = "template_ready"
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(card)))

    def test_blueprint_rejects_stale_foundation_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
            blueprint["foundation_lineage"][0]["card_sha256"] = "0" * 64
            write_json(blueprint_path, blueprint)
            self.assert_finding(root, "does not match current foundation card bytes")

    def test_candidate_foundation_cannot_feed_blueprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, card_path, blueprint_path = self.make_minimal_root(temporary)
            card = json.loads(card_path.read_text(encoding="utf-8"))
            card["status"] = "candidate"
            write_json(card_path, card)
            blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
            blueprint["foundation_lineage"][0]["card_sha256"] = file_hash(card_path)
            write_json(blueprint_path, blueprint)
            self.assert_finding(root, "candidate foundation cards cannot feed blueprints")

    def test_blueprint_rejects_duplicate_layers_and_omitted_gates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
            blueprint["complexity_layers"][1]["layer"] = "core"
            blueprint["proof_gates"] = [gate for gate in blueprint["proof_gates"] if gate["category"] != "leakage"]
            write_json(blueprint_path, blueprint)
            findings, _ = validate_repository(root)
            self.assertIn("layer names must be unique", "\n".join(findings))
            self.assertIn("must contain each required category exactly once", "\n".join(findings))

    def test_fixture_mapping_is_canonical_and_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, _ = self.make_minimal_root(temporary)
            positive = root / "examples/blueprints/fixtures/close-workbook.positive.json"
            fixture = json.loads(positive.read_text(encoding="utf-8"))
            fixture["archetype"] = "invented_nonproduction_type"
            write_json(positive, fixture)
            self.assert_finding(root, "must match the base blueprint archetype")
            positive.unlink()
            self.assert_finding(root, "requires exactly one pass and one fail fixture")

    def test_archetype_coverage_rejects_duplicates_and_omissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, _ = self.make_minimal_root(temporary)
            blueprint_path = root / "examples/blueprints/BP-0009.external-notice-approval.json"
            blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
            blueprint["archetype"] = "operational_note"
            write_json(blueprint_path, blueprint)
            for name in ("external-notice.positive.json", "external-notice.anti.json"):
                fixture_path = root / "examples/blueprints/fixtures" / name
                fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
                fixture["archetype"] = "operational_note"
                write_json(fixture_path, fixture)
            findings, _ = validate_repository(root)
            rendered = "\n".join(findings)
            self.assertIn("external_notice_approval: requires exactly one production blueprint; found 0", rendered)
            self.assertIn("operational_note: requires exactly one production blueprint; found 2", rendered)

    def test_typed_release_and_catalog_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, descriptor = self.make_release(root, blueprint_path)
            self.make_catalog(root, release_path, release, descriptor)
            self.assertEqual(validate_repository(root)[0], [])

    def test_descriptor_uses_direct_blueprint_reference_without_lineage_copy(self):
        schema = json.loads((ROOT / "schemas/template-descriptor.schema.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            _, _, _, descriptor = self.make_release(root, blueprint_path)
            descriptor["lineage"] = {"blueprint_id": "BP-0001", "foundation_ids": ["FOUND-0001"]}
            errors = list(Draft202012Validator(schema).iter_errors(descriptor))
            self.assertTrue(any("Additional properties" in error.message and "lineage" in error.message for error in errors))
            descriptor.pop("lineage")
            descriptor.pop("blueprint_id")
            errors = list(Draft202012Validator(schema).iter_errors(descriptor))
            self.assertTrue(any("blueprint_id" in error.message and "required property" in error.message for error in errors))

    def test_render_output_identity_requires_ordered_repository_paths(self):
        schema = json.loads((ROOT / "schemas/template-descriptor.schema.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            _, _, _, descriptor = self.make_release(root, blueprint_path)
            descriptor["render_contract"]["expected_pdf_path"] = "C:/private/render.pdf"
            descriptor["render_contract"]["expected_page_image_pattern"] = "evidence/pages/page.png"
            errors = list(Draft202012Validator(schema).iter_errors(descriptor))
            rendered = "\n".join(error.message for error in errors)
            self.assertIn("does not match", rendered)
            descriptor["render_contract"].pop("expected_pdf_path")
            descriptor["render_contract"].pop("expected_page_image_pattern")
            rendered = "\n".join(error.message for error in Draft202012Validator(schema).iter_errors(descriptor))
            self.assertIn("expected_pdf_path", rendered)
            self.assertIn("expected_page_image_pattern", rendered)

    def test_render_evidence_must_bind_descriptor_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, descriptor = self.make_release(root, blueprint_path)
            technical_reference = release["evidence"]["render"]
            technical_path = root / technical_reference["record_path"]
            technical = json.loads(technical_path.read_text(encoding="utf-8"))
            technical["render_contract_sha256"] = "0" * 64
            write_json(technical_path, technical)
            technical_hash = file_hash(technical_path)
            for reference in release["evidence"].values():
                reference["record_sha256"] = technical_hash
            write_json(release_path, release)
            self.make_catalog(root, release_path, release, descriptor)
            self.assert_finding(root, "must exactly bind the descriptor render contract")

    def test_population_contract_rejects_inverted_capacity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, descriptor_path, descriptor = self.make_release(root, blueprint_path)
            descriptor["population_contract"] = {
                "mode": "bounded_native_tables",
                "capacity_change_policy": "Rebuild and produce fresh proof before release.",
                "tables": [{"name": "Rows", "sheet": "Sheet1", "range": "A1:B2", "minimum_rows": 2, "maximum_rows": 1, "columns": {"ID": "nonempty_string"}}],
            }
            write_json(descriptor_path, descriptor)
            release["descriptor"]["sha256"] = file_hash(descriptor_path)
            write_json(release_path, release)
            self.assert_finding(root, "minimum_rows must not exceed maximum_rows")

    def test_fake_readme_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, _ = self.make_release(root, blueprint_path)
            readme = root / "README.md"
            readme.write_text("unrelated prose", encoding="utf-8")
            release["reviews"]["terra"] = {"record_path": "README.md", "record_sha256": file_hash(readme)}
            write_json(release_path, release)
            self.assert_finding(root, "path must be under evidence/template-releases")

    def test_distinct_review_records_and_lane_verdicts_are_enforced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, _ = self.make_release(root, blueprint_path)
            release["reviews"]["sol"] = release["reviews"]["terra"]
            write_json(release_path, release)
            self.assert_finding(root, "record paths must be distinct")
            terra_path = root / release["reviews"]["terra"]["record_path"]
            terra = json.loads(terra_path.read_text(encoding="utf-8"))
            terra["verdict"] = "INTEGRITY_PASS"
            write_json(terra_path, terra)
            release["reviews"]["terra"]["record_sha256"] = file_hash(terra_path)
            write_json(release_path, release)
            self.assert_finding(root, "'USABILITY_PASS' was expected")

    def test_reviewer_independence_uses_normalized_stable_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, _ = self.make_release(root, blueprint_path)
            references = {
                "terra": release["reviews"]["terra"],
                "sol": release["reviews"]["sol"],
                "conductor": release["conductor_approval"],
            }
            actor_ids = {"terra": "Reviewer:Same", "sol": "reviewer:same", "conductor": " REVIEWER:SAME "}
            for lane, reference in references.items():
                record_path = root / reference["record_path"]
                record = json.loads(record_path.read_text(encoding="utf-8"))
                record["actor_id"] = actor_ids[lane]
                write_json(record_path, record)
                reference["record_sha256"] = file_hash(record_path)
            write_json(release_path, release)
            self.assert_finding(root, "identities must be independent")

    def test_direct_blueprint_hash_sabotage_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, _ = self.make_release(root, blueprint_path)
            release["blueprint"]["sha256"] = "0" * 64
            write_json(release_path, release)
            self.assert_finding(root, "hash does not match")

    def test_catalog_released_entry_requires_exact_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, descriptor = self.make_release(root, blueprint_path)
            catalog_path, catalog = self.make_catalog(root, release_path, release, descriptor)
            del catalog["templates"][0]["release_record"]
            write_json(catalog_path, catalog)
            self.assert_finding(root, "release_record")

    def test_descriptor_id_hash_and_unbound_assets_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, descriptor_path, descriptor = self.make_release(root, blueprint_path)
            extra = descriptor_path.parent / "unbound.csv"
            extra.write_text("secret,world,facts", encoding="utf-8")
            descriptor["template_id"] = "TMPL-9999"
            descriptor["native_assets"].append({"path": extra.relative_to(root).as_posix(), "media_type": "text/csv", "sha256": file_hash(extra)})
            write_json(descriptor_path, descriptor)
            release["descriptor"]["sha256"] = file_hash(descriptor_path)
            write_json(release_path, release)
            findings, _ = validate_repository(root)
            rendered = "\n".join(findings)
            self.assertIn("descriptor.template_id", rendered)
            self.assertIn("must exactly match release native assets", rendered)

    def test_anonymous_file_in_template_version_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            _, _, descriptor_path, _ = self.make_release(root, blueprint_path)
            build_side = descriptor_path.parent.parent / "build/build.log"
            build_side.parent.mkdir(parents=True, exist_ok=True)
            build_side.write_text("build output\n", encoding="utf-8")
            self.assert_finding(root, "file is not bound by a catalog descriptor/native asset or release")
            build_side.unlink()
            anonymous = descriptor_path.parent / "anonymous-world-facts.csv"
            anonymous.write_text("private,answer\n", encoding="utf-8")
            self.assert_finding(root, "file is not bound by a catalog descriptor/native asset or release")

    def test_nested_shadow_json_in_governed_roots_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, _ = self.make_minimal_root(temporary)
            shadows = [
                root / "library/foundations/shadow/FOUND-9999.json",
                root / "examples/blueprints/shadow/BP-9999.shadow.json",
                root / "examples/blueprints/fixtures/shadow/close-workbook.positive.json",
            ]
            for path in shadows:
                write_json(path, {})
            rendered = "\n".join(validate_repository(root)[0])
            self.assertIn("unexpected foundation JSON filename or location", rendered)
            self.assertIn("unexpected blueprint or fixture JSON filename or location", rendered)

    def test_xlsx_descriptor_ghost_sheet_range_and_table_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, descriptor_path, descriptor = self.make_release(root, blueprint_path)
            descriptor["render_contract"]["expected_sheet_names"] = ["GHOST_SHEET"]
            descriptor["population_contract"] = {
                "mode": "bounded_native_tables",
                "capacity_change_policy": "Reject and rebuild with fresh proof before release.",
                "tables": [{"name": "GhostTable", "sheet": "GHOST_SHEET", "range": "A4:Z29", "minimum_rows": 1, "maximum_rows": 25, "columns": {"ID": "string"}}],
            }
            self.rebind_native_asset(root, release_path, release, descriptor_path, descriptor)
            self.make_catalog(root, release_path, release, descriptor)
            rendered = "\n".join(validate_repository(root)[0])
            self.assertIn("must exactly match native workbook sheet order", rendered)
            self.assertIn("does not resolve to a native workbook table", rendered)

    def test_required_render_rejects_missing_and_text_only_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, descriptor = self.make_release(root, blueprint_path)
            technical_reference = release["evidence"]["render"]
            technical_path = root / technical_reference["record_path"]
            technical = json.loads(technical_path.read_text(encoding="utf-8"))
            technical["artifacts"] = [item for item in technical["artifacts"] if item["category"] != "render"]
            text_proof = root / "evidence/template-releases/REL-0001/proofs/technical-render.txt"
            text_proof.write_text(
                f"release_id=REL-0001\ntemplate_id=TMPL-0001\ncategory=render\nnative_asset_sha256={release['native_assets'][0]['sha256']}\n",
                encoding="utf-8",
            )
            technical["artifacts"].append({"path": text_proof.relative_to(root).as_posix(), "sha256": file_hash(text_proof), "media_type": "text/plain", "category": "render"})
            write_json(technical_path, technical)
            technical_hash = file_hash(technical_path)
            for reference in release["evidence"].values():
                reference["record_sha256"] = technical_hash
            for path in (root / descriptor["render_contract"]["expected_pdf_path"], root / descriptor["render_contract"]["expected_page_image_pattern"].format(page=1)):
                path.unlink()
            write_json(release_path, release)
            self.make_catalog(root, release_path, release, descriptor)
            rendered = "\n".join(validate_repository(root)[0])
            self.assertIn("text-only proof is not sufficient", rendered)
            self.assertIn("referenced file does not exist", rendered)

    def test_sanitizer_cannot_alias_terra_reviewer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, descriptor = self.make_release(root, blueprint_path)
            sanitizer_reference = release["sanitization"]["evidence"]
            sanitizer_path = root / sanitizer_reference["record_path"]
            sanitizer = json.loads(sanitizer_path.read_text(encoding="utf-8"))
            sanitizer["actor_id"] = " REVIEWER:TERRA "
            write_json(sanitizer_path, sanitizer)
            sanitizer_reference["record_sha256"] = file_hash(sanitizer_path)
            write_json(release_path, release)
            self.make_catalog(root, release_path, release, descriptor)
            self.assert_finding(root, "build, sanitization, Terra, Sol, and conductor identities must be independent")

    def test_actor_id_has_one_stable_display_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, descriptor = self.make_release(root, blueprint_path)
            technical_reference = release["evidence"]["render"]
            technical_path = root / technical_reference["record_path"]
            technical = json.loads(technical_path.read_text(encoding="utf-8"))
            technical["actor_id"] = " BUILDER:TEMPLATE "
            technical["actor"] = "Unrelated validation identity"
            write_json(technical_path, technical)
            technical_hash = file_hash(technical_path)
            for reference in release["evidence"].values():
                reference["record_sha256"] = technical_hash
            write_json(release_path, release)
            self.make_catalog(root, release_path, release, descriptor)
            self.assert_finding(root, "actor_id must map to one stable actor name")

    def test_catalog_mismatched_id_descriptor_and_asset_hashes_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, descriptor = self.make_release(root, blueprint_path)
            catalog_path, catalog = self.make_catalog(root, release_path, release, descriptor)
            entry = catalog["templates"][0]
            entry["template_id"] = "TMPL-9999"
            entry["descriptor"] = "library/templates/TMPL-0001/1.0.0/missing.json"
            entry["native_assets"] = ["library/templates/TMPL-0001/1.0.0/missing.xlsx"]
            write_json(catalog_path, catalog)
            findings, _ = validate_repository(root)
            rendered = "\n".join(findings)
            self.assertIn("template_id: does not match release", rendered)
            self.assertIn("descriptor: does not match release", rendered)
            self.assertIn("native_assets: do not match release", rendered)

    def test_rich_candidate_catalog_and_descriptor_pass_without_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, descriptor_path, descriptor = self.make_release(root, blueprint_path)
            descriptor["release_status"] = "candidate"
            write_json(descriptor_path, descriptor)
            catalog_path, catalog = self.make_catalog(root, release_path, release, descriptor)
            release_path.unlink()
            evidence_root = root / "evidence/template-releases/REL-0001"
            for evidence_path in evidence_root.glob("*.json"):
                if evidence_path.name != "render-manifest.json":
                    evidence_path.unlink()
            shutil.rmtree(evidence_root / "proofs")
            entry = catalog["templates"][0]
            entry["release_status"] = "candidate"
            entry.pop("release_record")
            write_json(catalog_path, catalog)
            self.assertEqual(validate_repository(root)[0], [])

    def test_candidate_catalog_paths_and_hashes_are_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, descriptor_path, descriptor = self.make_release(root, blueprint_path)
            descriptor["release_status"] = "candidate"
            write_json(descriptor_path, descriptor)
            catalog_path, catalog = self.make_catalog(root, release_path, release, descriptor)
            release_path.unlink()
            entry = catalog["templates"][0]
            entry["release_status"] = "candidate"
            entry.pop("release_record")
            entry["descriptor"] = "library/templates/TMPL-0001/1.0.0/missing.json"
            entry["native_assets"] = ["library/templates/TMPL-0001/1.0.0/missing.xlsx"]
            write_json(catalog_path, catalog)
            findings, _ = validate_repository(root)
            self.assertIn("referenced file does not exist", "\n".join(findings))

    def test_every_release_must_appear_exactly_once_in_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            self.make_release(root, blueprint_path)
            write_json(root / "library/catalog.json", {"schema_version": "1.0.0", "catalog_id": "syn-studios-artifact-library", "discovery_fields": ["artifact_type"], "templates": []})
            self.assert_finding(root, "release must appear exactly once in library/catalog.json; found 0")

    def test_generic_evidence_record_without_outputs_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, descriptor = self.make_release(root, blueprint_path)
            self.make_catalog(root, release_path, release, descriptor)
            technical_path = root / release["evidence"]["render"]["record_path"]
            technical = json.loads(technical_path.read_text(encoding="utf-8"))
            technical.pop("observations")
            technical.pop("artifacts")
            write_json(technical_path, technical)
            new_hash = file_hash(technical_path)
            for reference in release["evidence"].values():
                reference["record_sha256"] = new_hash
            write_json(release_path, release)
            self.assert_finding(root, "'artifacts' is a required property")

    def test_hash_consistent_fake_xlsx_is_rejected_by_native_shape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, descriptor_path, descriptor = self.make_release(root, blueprint_path)
            asset_path = root / release["native_assets"][0]["path"]
            asset_path.write_bytes(b"authorized synthetic workbook template")
            asset_hash = file_hash(asset_path)
            release["native_assets"][0]["sha256"] = asset_hash
            descriptor["native_assets"][0]["sha256"] = asset_hash
            write_json(descriptor_path, descriptor)
            descriptor_hash = file_hash(descriptor_path)
            release["descriptor"]["sha256"] = descriptor_hash
            evidence_references = [release["build"], release["sanitization"]["evidence"], release["reviews"]["terra"], release["reviews"]["sol"], release["conductor_approval"], *release["evidence"].values()]
            seen_paths = set()
            for reference in evidence_references:
                evidence_path = root / reference["record_path"]
                if evidence_path in seen_paths:
                    continue
                seen_paths.add(evidence_path)
                record = json.loads(evidence_path.read_text(encoding="utf-8"))
                record["descriptor_sha256"] = descriptor_hash
                record["native_asset_sha256s"] = [asset_hash]
                write_json(evidence_path, record)
            for reference in evidence_references:
                reference["record_sha256"] = file_hash(root / reference["record_path"])
            write_json(release_path, release)
            self.make_catalog(root, release_path, release, descriptor)
            self.assert_finding(root, "is not a valid xlsx OOXML package")

    def test_hash_consistent_malformed_ooxml_members_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, descriptor_path, descriptor = self.make_release(root, blueprint_path)
            asset_path = root / release["native_assets"][0]["path"]
            with zipfile.ZipFile(asset_path, "w") as package:
                package.writestr("[Content_Types].xml", "not xml")
                package.writestr("xl/workbook.xml", "not xml")
            self.rebind_native_asset(root, release_path, release, descriptor_path, descriptor)
            self.make_catalog(root, release_path, release, descriptor)
            self.assert_finding(root, "contains malformed xlsx OOXML")

    def test_docx_impostor_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "impostor.docx"
            with zipfile.ZipFile(path, "w") as package:
                package.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
                package.writestr("word/document.xml", "<document><body/></document>")
            findings = []
            validate_native_asset_shape(path, "docx", "impostor", findings)
            self.assertIn("invalid WordprocessingML", "\n".join(findings))

    def test_eml_impostor_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "impostor.eml"
            path.write_bytes(b"From: sender@example.test\r\nSubject: Incomplete\r\n\r\n")
            findings = []
            validate_native_asset_shape(path, "eml", "impostor", findings)
            self.assertIn("EML must have valid", "\n".join(findings))

    def test_binary_render_impostors_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            png = root / "render.png"
            pdf = root / "render.pdf"
            png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 120)
            pdf.write_bytes(b"%PDF-1.7\n" + b"x" * 120 + b"\n%%EOF")
            release = {"release_id": "REL-0001", "template_id": "TMPL-0001"}
            findings = []
            validate_proof_artifact(png, {"media_type": "image/png", "category": "render"}, release, set(), "png", findings)
            validate_proof_artifact(pdf, {"media_type": "application/pdf", "category": "render"}, release, set(), "pdf", findings)
            rendered = "\n".join(findings)
            self.assertIn("PNG proof", rendered)
            self.assertIn("PDF lacks meaningful", rendered)

    def test_one_byte_generic_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, descriptor = self.make_release(root, blueprint_path)
            technical_reference = release["evidence"]["render"]
            technical_path = root / technical_reference["record_path"]
            technical = json.loads(technical_path.read_text(encoding="utf-8"))
            proof_path = root / technical["artifacts"][0]["path"]
            proof_path.write_bytes(b"x")
            technical["artifacts"][0]["sha256"] = file_hash(proof_path)
            write_json(technical_path, technical)
            technical_hash = file_hash(technical_path)
            for reference in release["evidence"].values():
                reference["record_sha256"] = technical_hash
            write_json(release_path, release)
            self.make_catalog(root, release_path, release, descriptor)
            self.assert_finding(root, "proof output is too small to be meaningful")

    def test_unbound_generic_text_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, descriptor = self.make_release(root, blueprint_path)
            technical_reference = release["evidence"]["render"]
            technical_path = root / technical_reference["record_path"]
            technical = json.loads(technical_path.read_text(encoding="utf-8"))
            proof_path = root / technical["artifacts"][0]["path"]
            proof_path.write_text("Generic evidence output with no release, template, category, or asset binding. " * 3, encoding="utf-8")
            technical["artifacts"][0]["sha256"] = file_hash(proof_path)
            write_json(technical_path, technical)
            technical_hash = file_hash(technical_path)
            for reference in release["evidence"].values():
                reference["record_sha256"] = technical_hash
            write_json(release_path, release)
            self.make_catalog(root, release_path, release, descriptor)
            self.assert_finding(root, "text proof does not bind REL-0001")

    def test_unicode_equivalent_reviewer_aliases_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, descriptor = self.make_release(root, blueprint_path)
            aliases = {"terra": "r\u00e9viewer:same", "sol": "re\u0301viewer:same"}
            self.assertEqual(unicodedata.normalize("NFKC", aliases["terra"]), unicodedata.normalize("NFKC", aliases["sol"]))
            for lane, actor_id in aliases.items():
                reference = release["reviews"][lane]
                record_path = root / reference["record_path"]
                record = json.loads(record_path.read_text(encoding="utf-8"))
                record["actor_id"] = actor_id
                write_json(record_path, record)
                reference["record_sha256"] = file_hash(record_path)
            write_json(release_path, release)
            self.make_catalog(root, release_path, release, descriptor)
            self.assert_finding(root, "identities must be independent")

    def test_unexpected_release_and_evidence_json_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, _ = self.make_minimal_root(temporary)
            write_json(root / "library/releases/release.json", {})
            write_json(root / "evidence/template-releases/REL-9999/shadow.json", {})
            findings, _ = validate_repository(root)
            rendered = "\n".join(findings)
            self.assertIn("unexpected release JSON filename or location", rendered)
            self.assertIn("evidence JSON is not bound by a release record", rendered)

    def test_evidence_record_ids_are_unique_across_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _, blueprint_path = self.make_minimal_root(temporary)
            release_path, release, _, descriptor = self.make_release(root, blueprint_path)
            terra_path = root / release["reviews"]["terra"]["record_path"]
            sol_path = root / release["reviews"]["sol"]["record_path"]
            terra = json.loads(terra_path.read_text(encoding="utf-8"))
            sol = json.loads(sol_path.read_text(encoding="utf-8"))
            sol["record_id"] = terra["record_id"]
            write_json(sol_path, sol)
            release["reviews"]["sol"]["record_sha256"] = file_hash(sol_path)
            write_json(release_path, release)
            self.make_catalog(root, release_path, release, descriptor)
            self.assert_finding(root, "duplicate evidence identity")

    def test_malformed_unhashable_ids_report_without_crashing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, card_path, blueprint_path = self.make_minimal_root(temporary)
            card = json.loads(card_path.read_text(encoding="utf-8"))
            card["card_id"] = ["FOUND-0001"]
            write_json(card_path, card)
            blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
            blueprint["blueprint_id"] = {"bad": "id"}
            blueprint["foundation_lineage"][0]["card_id"] = ["FOUND-0001"]
            write_json(blueprint_path, blueprint)
            findings, _ = validate_repository(root)
            self.assertTrue(findings)
            self.assertIn("is not of type 'string'", "\n".join(findings))


if __name__ == "__main__":
    unittest.main()
