import base64
import json
import re
import shutil
import tempfile
import unittest
import zipfile
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from xml.etree import ElementTree as ET

from scripts.run_template_technical_validation import ValidationFailed, run, sha256


ROOT = Path(__file__).resolve().parents[1]
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def minimal_compact_pdf(newline: bytes) -> bytes:
    pdf = bytearray(b"\xef\xbb\xbf%PDF-1.4" + newline)
    offsets = []
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] /Contents 4 0 R >>",
        b"<< /Length 0 >>" + newline + b"stream" + newline + newline + b"endstream",
    )
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj".encode("ascii"))
        pdf.extend(body + newline + b"endobj" + newline)
    xref_offset = len(pdf)
    pdf.extend(b"xref" + newline + f"0 {len(objects) + 1}".encode("ascii") + newline)
    pdf.extend(b"0000000000 65535 f " + newline)
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n ".encode("ascii") + newline)
    pdf.extend(b"trailer" + newline)
    pdf.extend(f"<< /Size {len(objects) + 1} /Root 1 0 R >>".encode("ascii") + newline)
    pdf.extend(b"startxref" + newline + str(xref_offset).encode("ascii") + newline + b"%%EOF" + newline)
    return bytes(pdf)


class TechnicalValidationRunnerTests(unittest.TestCase):
    actor_id = "syn-validation-runner-2026-08-29"
    actor = "Syn Studios validation runner"

    def copy_release_inputs(self, directory: str) -> Path:
        root = Path(directory).resolve()
        for relative in ("library/releases", "library/templates", "library/foundations", "examples/blueprints"):
            shutil.copytree(ROOT / relative, root / relative)
        evidence = root / "evidence/template-releases"
        for source in sorted((ROOT / "evidence/template-releases").glob("REL-*")):
            target = evidence / source.name
            target.mkdir(parents=True)
            manifest = source / "render-manifest.json"
            if manifest.is_file():
                shutil.copy2(manifest, target / manifest.name)
            if (source / "render").is_dir():
                shutil.copytree(source / "render", target / "render")
        return root

    def release(self, root: Path, release_id: str) -> tuple[Path, dict, Path, dict, Path]:
        release_path = root / f"library/releases/{release_id}.template.json"
        release = json.loads(release_path.read_text(encoding="utf-8"))
        descriptor_path = root / release["descriptor"]["path"]
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        asset_path = root / descriptor["native_assets"][0]["path"]
        return release_path, release, descriptor_path, descriptor, asset_path

    def rebind_asset(self, root: Path, release_id: str) -> None:
        release_path, release, descriptor_path, descriptor, asset_path = self.release(root, release_id)
        asset_hash = sha256(asset_path)
        descriptor["native_assets"][0]["sha256"] = asset_hash
        write_json(descriptor_path, descriptor)
        release["descriptor"]["sha256"] = sha256(descriptor_path)
        release["native_assets"][0]["sha256"] = asset_hash
        write_json(release_path, release)
        manifest_relative = descriptor["render_contract"].get("evidence_manifest")
        if manifest_relative:
            manifest_path = root / manifest_relative
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["templates"][descriptor["template_id"]]["asset_sha256"] = asset_hash
            write_json(manifest_path, manifest)

    def assert_refused_without_results(self, root: Path) -> None:
        with self.assertRaises(ValidationFailed):
            run(root, self.actor_id, self.actor, write=True)
        self.assertEqual(list(root.glob("evidence/template-releases/REL-*/technical-results/*.json")), [])

    def mutate_zip_member(self, asset: Path, member: str, mutation) -> None:
        with zipfile.ZipFile(asset) as package:
            members = {info.filename: package.read(info.filename) for info in package.infolist()}
        tree = ET.fromstring(members[member])
        mutation(tree)
        members[member] = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
        with zipfile.ZipFile(asset, "w") as package:
            for name, payload in members.items():
                package.writestr(name, payload)

    def test_real_release_inputs_dry_run_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            paths = run(root, self.actor_id, self.actor)
            self.assertEqual(len(paths), 24)
            self.assertTrue(all(not path.exists() for path in paths))

    def test_write_creates_24_deterministic_category_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            paths = run(root, self.actor_id, self.actor, write=True)
            self.assertEqual(len(paths), 24)
            first = {path.relative_to(root).as_posix(): path.read_bytes() for path in paths}
            paths = run(root, self.actor_id, self.actor, write=True)
            second = {path.relative_to(root).as_posix(): path.read_bytes() for path in paths}
            self.assertEqual(first, second)
            for path in paths:
                result = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(result["actor_id"], self.actor_id)
                self.assertTrue(result["checks"])
                self.assertTrue(all(item["id"].startswith(result["category"] + ":") for item in result["checks"]))

    def test_invalid_render_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            png = root / "evidence/template-releases/REL-0002/render/internal-controller-memo-page-3.png"
            png.write_bytes(b"not a PNG")
            self.assert_refused_without_results(root)

    def test_missing_formula_cache_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0001")
            with zipfile.ZipFile(asset) as package:
                members = {info.filename: package.read(info.filename) for info in package.infolist()}
            worksheet = ET.fromstring(members["xl/worksheets/sheet8.xml"])
            formula_cell = next(cell for cell in worksheet.findall(f".//{{{MAIN}}}c") if cell.find(f"{{{MAIN}}}f") is not None)
            formula_cell.remove(formula_cell.find(f"{{{MAIN}}}v"))
            members["xl/worksheets/sheet8.xml"] = ET.tostring(worksheet, encoding="utf-8", xml_declaration=True)
            with zipfile.ZipFile(asset, "w") as package:
                for name, payload in members.items():
                    package.writestr(name, payload)
            self.rebind_asset(root, "REL-0001")
            self.assert_refused_without_results(root)

    def test_empty_formula_cache_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0001")

            def empty_cache(tree):
                formula_cell = next(cell for cell in tree.findall(f".//{{{MAIN}}}c") if cell.find(f"{{{MAIN}}}f") is not None)
                formula_cell.find(f"{{{MAIN}}}v").text = None

            self.mutate_zip_member(asset, "xl/worksheets/sheet6.xml", empty_cache)
            self.rebind_asset(root, "REL-0001")
            self.assert_refused_without_results(root)

    def test_numeric_formula_cache_cannot_be_spoofed_as_empty_string(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0001")

            def spoof_cache(tree):
                formula_cell = next(cell for cell in tree.findall(f".//{{{MAIN}}}c") if cell.find(f"{{{MAIN}}}f") is not None)
                formula_cell.set("t", "str")
                formula_cell.find(f"{{{MAIN}}}v").text = None

            self.mutate_zip_member(asset, "xl/worksheets/sheet6.xml", spoof_cache)
            self.rebind_asset(root, "REL-0001")
            self.assert_refused_without_results(root)

    def test_true_lexical_hidden_row_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0001")

            def hide_row(tree):
                tree.find(f".//{{{MAIN}}}row").set("hidden", "true")

            self.mutate_zip_member(asset, "xl/worksheets/sheet1.xml", hide_row)
            self.rebind_asset(root, "REL-0001")
            self.assert_refused_without_results(root)

    def test_external_link_surface_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0001")
            with zipfile.ZipFile(asset, "a") as package:
                package.writestr("xl/externalLinks/externalLink1.xml", "<externalLink/>")
            self.rebind_asset(root, "REL-0001")
            self.assert_refused_without_results(root)

    def test_external_xlsx_relationship_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0001")
            with zipfile.ZipFile(asset) as package:
                members = {info.filename: package.read(info.filename) for info in package.infolist()}
            relationships = ET.fromstring(members["xl/_rels/workbook.xml.rels"])
            ET.SubElement(
                relationships,
                f"{{{PACKAGE_REL}}}Relationship",
                {
                    "Id": "rIdExternalReview",
                    "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/externalLink",
                    "Target": "https://example.invalid/external-review.xlsx",
                    "TargetMode": "External",
                },
            )
            members["xl/_rels/workbook.xml.rels"] = ET.tostring(
                relationships, encoding="utf-8", xml_declaration=True
            )
            with zipfile.ZipFile(asset, "w") as package:
                for name, payload in members.items():
                    package.writestr(name, payload)
            self.rebind_asset(root, "REL-0001")
            self.assert_refused_without_results(root)

    def test_prohibited_xlsx_header_footer_text_is_refused_before_any_write(self):
        for tag in ("oddHeader", "oddFooter"):
            with self.subTest(tag=tag), tempfile.TemporaryDirectory() as directory:
                root = self.copy_release_inputs(directory)
                _, _, _, _, asset = self.release(root, "REL-0001")
                with zipfile.ZipFile(asset) as package:
                    members = {info.filename: package.read(info.filename) for info in package.infolist()}
                worksheet = ET.fromstring(members["xl/worksheets/sheet1.xml"])
                header_footer = worksheet.find(f"{{{MAIN}}}headerFooter")
                if header_footer is None:
                    header_footer = ET.SubElement(worksheet, f"{{{MAIN}}}headerFooter")
                ET.SubElement(header_footer, f"{{{MAIN}}}{tag}").text = "&Lprivate grading answer key"
                members["xl/worksheets/sheet1.xml"] = ET.tostring(
                    worksheet, encoding="utf-8", xml_declaration=True
                )
                with zipfile.ZipFile(asset, "w") as package:
                    for name, payload in members.items():
                        package.writestr(name, payload)
                self.rebind_asset(root, "REL-0001")
                self.assert_refused_without_results(root)

    def test_prohibited_xlsx_relationship_or_vml_text_is_refused_before_any_write(self):
        members = (
            ("xl/worksheets/_rels/sheet1.xml.rels", "Relationships"),
            ("xl/drawings/review-shape.vml", "xml"),
        )
        for member, root_name in members:
            with self.subTest(member=member), tempfile.TemporaryDirectory() as directory:
                root = self.copy_release_inputs(directory)
                _, _, _, _, asset = self.release(root, "REL-0001")
                with zipfile.ZipFile(asset) as package:
                    package_members = {
                        info.filename: package.read(info.filename)
                        for info in package.infolist()
                        if info.filename != member
                    }
                hidden = ET.Element(root_name)
                hidden.set("review-note", "private grading answer key")
                package_members[member] = ET.tostring(hidden, encoding="utf-8", xml_declaration=True)
                with zipfile.ZipFile(asset, "w") as package:
                    for name, payload in package_members.items():
                        package.writestr(name, payload)
                self.rebind_asset(root, "REL-0001")
                self.assert_refused_without_results(root)

    def test_undeclared_token_split_across_xlsx_inline_runs_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0001")

            def add_split_token(tree):
                sheet_data = tree.find(f"{{{MAIN}}}sheetData")
                row = ET.SubElement(sheet_data, f"{{{MAIN}}}row", {"r": "99"})
                cell = ET.SubElement(row, f"{{{MAIN}}}c", {"r": "Z99", "t": "inlineStr"})
                inline = ET.SubElement(cell, f"{{{MAIN}}}is")
                for value in ("{", "{UNDECLARED_SECRET}}"):
                    run_element = ET.SubElement(inline, f"{{{MAIN}}}r")
                    ET.SubElement(run_element, f"{{{MAIN}}}t").text = value

            self.mutate_zip_member(asset, "xl/worksheets/sheet1.xml", add_split_token)
            self.rebind_asset(root, "REL-0001")
            self.assert_refused_without_results(root)

    def test_prohibited_leakage_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0003")
            asset.write_bytes(asset.read_bytes() + b"\nX-Note: private grading residue\n")
            self.rebind_asset(root, "REL-0003")
            self.assert_refused_without_results(root)

    def test_prohibited_docx_text_part_is_refused_before_any_write(self):
        xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:root xmlns:w="{WORD}">'
            '<w:p><w:r><w:t>private grading answer key</w:t></w:r></w:p></w:root>'
        ).encode("utf-8")
        for member in ("word/header1.xml", "word/footer1.xml", "word/footnotes.xml", "word/endnotes.xml"):
            with self.subTest(member=member), tempfile.TemporaryDirectory() as directory:
                root = self.copy_release_inputs(directory)
                _, _, _, _, asset = self.release(root, "REL-0002")
                with zipfile.ZipFile(asset) as package:
                    members = [(info, package.read(info.filename)) for info in package.infolist() if info.filename != member]
                with zipfile.ZipFile(asset, "w") as package:
                    for info, payload in members:
                        package.writestr(info, payload)
                    package.writestr(member, xml)
                self.rebind_asset(root, "REL-0002")
                self.assert_refused_without_results(root)

    def test_undeclared_uppercase_token_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0002")

            def add_token(tree):
                paragraph = ET.SubElement(tree, f"{{{WORD}}}p")
                run_element = ET.SubElement(paragraph, f"{{{WORD}}}r")
                ET.SubElement(run_element, f"{{{WORD}}}t").text = "{{UNDECLARED_SECRET}}"

            self.mutate_zip_member(asset, "word/header1.xml", add_token)
            self.rebind_asset(root, "REL-0002")
            self.assert_refused_without_results(root)

    def test_undeclared_token_split_across_docx_runs_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0002")

            def add_split_token(tree):
                paragraph = ET.SubElement(tree, f"{{{WORD}}}p")
                for value in ("{", "{UNDECLARED_SECRET}}"):
                    run_element = ET.SubElement(paragraph, f"{{{WORD}}}r")
                    ET.SubElement(run_element, f"{{{WORD}}}t").text = value

            self.mutate_zip_member(asset, "word/header1.xml", add_split_token)
            self.rebind_asset(root, "REL-0002")
            self.assert_refused_without_results(root)

    def test_prohibited_docx_custom_xml_or_hidden_header_is_refused_before_any_write(self):
        for member, payload in (
            ("customXml/item1.xml", b'<review note="private grading answer key"/>'),
            (
                "word/header1.xml",
                (
                    f'<w:root xmlns:w="{WORD}"><w:r><w:rPr><w:vanish/></w:rPr>'
                    '<w:t>concealed review note</w:t></w:r></w:root>'
                ).encode("utf-8"),
            ),
        ):
            with self.subTest(member=member), tempfile.TemporaryDirectory() as directory:
                root = self.copy_release_inputs(directory)
                _, _, _, _, asset = self.release(root, "REL-0002")
                with zipfile.ZipFile(asset) as package:
                    package_members = {
                        info.filename: package.read(info.filename)
                        for info in package.infolist()
                        if info.filename != member
                    }
                package_members[member] = payload
                with zipfile.ZipFile(asset, "w") as package:
                    for name, member_payload in package_members.items():
                        package.writestr(name, member_payload)
                self.rebind_asset(root, "REL-0002")
                self.assert_refused_without_results(root)

    def test_docx_altchunk_content_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0002")
            with zipfile.ZipFile(asset) as package:
                members = {info.filename: package.read(info.filename) for info in package.infolist()}
            document = ET.fromstring(members["word/document.xml"])
            body = document.find(f"{{{WORD}}}body")
            ET.SubElement(body, f"{{{WORD}}}altChunk", {f"{{{OFFICE_REL}}}id": "rIdAltChunkReview"})
            members["word/document.xml"] = ET.tostring(document, encoding="utf-8", xml_declaration=True)
            relationships = ET.fromstring(members["word/_rels/document.xml.rels"])
            ET.SubElement(
                relationships,
                f"{{{PACKAGE_REL}}}Relationship",
                {
                    "Id": "rIdAltChunkReview",
                    "Type": f"{OFFICE_REL}/aFChunk",
                    "Target": "afchunk1.html",
                },
            )
            members["word/_rels/document.xml.rels"] = ET.tostring(
                relationships, encoding="utf-8", xml_declaration=True
            )
            members["word/afchunk1.html"] = b"<html><body>private grading answer key</body></html>"
            with zipfile.ZipFile(asset, "w") as package:
                for name, payload in members.items():
                    package.writestr(name, payload)
            self.rebind_asset(root, "REL-0002")
            self.assert_refused_without_results(root)

    def test_prohibited_secondary_docx_asset_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            release_path, release, descriptor_path, descriptor, primary = self.release(root, "REL-0002")
            secondary = primary.with_name("supporting-controller-notes.docx")
            shutil.copy2(primary, secondary)
            with zipfile.ZipFile(secondary) as package:
                members = {info.filename: package.read(info.filename) for info in package.infolist()}
            header = ET.fromstring(members["word/header1.xml"])
            paragraph = ET.SubElement(header, f"{{{WORD}}}p")
            run_element = ET.SubElement(paragraph, f"{{{WORD}}}r")
            ET.SubElement(run_element, f"{{{WORD}}}t").text = "private grading answer key"
            members["word/header1.xml"] = ET.tostring(header, encoding="utf-8", xml_declaration=True)
            with zipfile.ZipFile(secondary, "w") as package:
                for name, payload in members.items():
                    package.writestr(name, payload)

            relative = secondary.relative_to(root).as_posix()
            digest = sha256(secondary)
            descriptor["native_assets"].append(
                {
                    "path": relative,
                    "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "sha256": digest,
                }
            )
            write_json(descriptor_path, descriptor)
            release["descriptor"]["sha256"] = sha256(descriptor_path)
            release["native_assets"].append({"path": relative, "sha256": digest})
            write_json(release_path, release)
            self.assert_refused_without_results(root)

    def test_prohibited_binary_eml_attachment_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0003")
            message = BytesParser(policy=policy.default).parsebytes(asset.read_bytes())
            message.add_attachment(
                b"private grading answer key",
                maintype="application",
                subtype="octet-stream",
                filename="review-cache.bin",
            )
            asset.write_bytes(message.as_bytes(policy=policy.default))
            self.rebind_asset(root, "REL-0003")
            self.assert_refused_without_results(root)

    def test_compressed_eml_attachment_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0003")
            message = BytesParser(policy=policy.default).parsebytes(asset.read_bytes())
            message.add_attachment(
                b"PK\x03\x04compressed review payload",
                maintype="application",
                subtype="zip",
                filename="review-cache.zip",
            )
            asset.write_bytes(message.as_bytes(policy=policy.default))
            self.rebind_asset(root, "REL-0003")
            self.assert_refused_without_results(root)

    def test_encoded_eml_subject_token_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0003")
            encoded = base64.b64encode(b"{{UNDECLARED_SECRET}}").decode("ascii")
            payload, count = re.subn(
                rb"(?im)^Subject:[^\r\n]*(?:\r?\n[ \t][^\r\n]*)*",
                f"Subject: =?utf-8?B?{encoded}?=".encode("ascii"),
                asset.read_bytes(),
                count=1,
            )
            self.assertEqual(count, 1)
            asset.write_bytes(payload)
            self.rebind_asset(root, "REL-0003")
            self.assert_refused_without_results(root)

    def test_binary_signature_mislabeled_as_text_attachment_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0003")
            message = BytesParser(policy=policy.default).parsebytes(asset.read_bytes())
            attachment = EmailMessage(policy=policy.default)
            attachment["Content-Type"] = 'text/plain; charset="iso-8859-1"'
            attachment["Content-Disposition"] = 'attachment; filename="review-cache.txt"'
            attachment["Content-Transfer-Encoding"] = "base64"
            attachment.set_payload(base64.b64encode(b"PK\x03\x04compressed review payload").decode("ascii"))
            message.attach(attachment)
            asset.write_bytes(message.as_bytes(policy=policy.default))
            self.rebind_asset(root, "REL-0003")
            self.assert_refused_without_results(root)

    def test_structured_executable_mislabeled_as_text_attachment_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0003")
            message = BytesParser(policy=policy.default).parsebytes(asset.read_bytes())
            executable = bytearray(128)
            executable[:2] = b"MZ"
            executable[60:64] = (64).to_bytes(4, "little")
            executable[64:68] = b"PE\x00\x00"
            attachment = EmailMessage(policy=policy.default)
            attachment["Content-Type"] = 'text/plain; charset="iso-8859-1"'
            attachment["Content-Disposition"] = 'attachment; filename="review-cache.txt"'
            attachment["Content-Transfer-Encoding"] = "base64"
            attachment.set_payload(base64.b64encode(executable).decode("ascii"))
            message.attach(attachment)
            asset.write_bytes(message.as_bytes(policy=policy.default))
            self.rebind_asset(root, "REL-0003")
            self.assert_refused_without_results(root)

    def test_valid_mz_prefixed_csv_attachment_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0003")
            message = BytesParser(policy=policy.default).parsebytes(asset.read_bytes())
            message.add_attachment(
                (
                    "MZ Account,Amount,Description\r\n"
                    "MZ-100,2,Opening balance correction\r\n"
                    "MZ-200,3,Follow-up entry\r\n"
                ),
                subtype="csv",
                filename="mz-source.csv",
            )
            asset.write_bytes(message.as_bytes(policy=policy.default))
            self.rebind_asset(root, "REL-0003")

            self.assertEqual(len(run(root, self.actor_id, self.actor)), 24)

    def test_valid_printable_signature_prefixed_csv_attachment_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0003")
            message = BytesParser(policy=policy.default).parsebytes(asset.read_bytes())
            message.add_attachment(
                "GIF89a Metric,Amount\r\nOpen items,2\r\nClosed items,7\r\n",
                subtype="csv",
                filename="status-metrics.csv",
            )
            asset.write_bytes(message.as_bytes(policy=policy.default))
            self.rebind_asset(root, "REL-0003")

            self.assertEqual(len(run(root, self.actor_id, self.actor)), 24)

    def test_pdf_with_legal_preheader_and_compact_objects_mislabeled_as_text_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0003")
            message = BytesParser(policy=policy.default).parsebytes(asset.read_bytes())
            attachment = EmailMessage(policy=policy.default)
            attachment["Content-Type"] = 'text/plain; charset="iso-8859-1"'
            attachment["Content-Disposition"] = 'attachment; filename="review.txt"'
            attachment["Content-Transfer-Encoding"] = "base64"
            attachment.set_payload(base64.b64encode(minimal_compact_pdf(b"\n")).decode("ascii"))
            message.attach(attachment)
            asset.write_bytes(message.as_bytes(policy=policy.default))
            self.rebind_asset(root, "REL-0003")
            self.assert_refused_without_results(root)

    def test_cr_only_compact_pdf_mislabeled_as_text_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0003")
            message = BytesParser(policy=policy.default).parsebytes(asset.read_bytes())
            attachment = EmailMessage(policy=policy.default)
            attachment["Content-Type"] = 'text/plain; charset="iso-8859-1"'
            attachment["Content-Disposition"] = 'attachment; filename="cr-only-review.txt"'
            attachment["Content-Transfer-Encoding"] = "base64"
            attachment.set_payload(base64.b64encode(minimal_compact_pdf(b"\r")).decode("ascii"))
            message.attach(attachment)
            asset.write_bytes(message.as_bytes(policy=policy.default))
            self.rebind_asset(root, "REL-0003")
            self.assert_refused_without_results(root)

    def test_pdf_object_prefix_in_text_is_not_classified_as_structured_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0003")
            message = BytesParser(policy=policy.default).parsebytes(asset.read_bytes())
            text = bytearray(b"%PDF-1.4\n1 0 objective status remains open\n")
            xref_offset = len(text)
            text.extend(b"xref narrative section\n")
            text.extend(f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
            attachment = EmailMessage(policy=policy.default)
            attachment["Content-Type"] = 'text/plain; charset="utf-8"'
            attachment["Content-Disposition"] = 'attachment; filename="pdf-review-notes.txt"'
            attachment["Content-Transfer-Encoding"] = "base64"
            attachment.set_payload(base64.b64encode(text).decode("ascii"))
            message.attach(attachment)
            asset.write_bytes(message.as_bytes(policy=policy.default))
            self.rebind_asset(root, "REL-0003")

            self.assertEqual(len(run(root, self.actor_id, self.actor)), 24)

    def test_valid_utf16_text_attachment_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0003")
            message = BytesParser(policy=policy.default).parsebytes(asset.read_bytes())
            attachment = EmailMessage(policy=policy.default)
            attachment["Content-Type"] = 'text/plain; charset="utf-16"'
            attachment["Content-Disposition"] = 'attachment; filename="unicode-notes.txt"'
            attachment["Content-Transfer-Encoding"] = "base64"
            attachment.set_payload(
                base64.b64encode("Owner\tStatus\r\nAnalyst\tOpen\r\n".encode("utf-16")).decode("ascii")
            )
            message.attach(attachment)
            asset.write_bytes(message.as_bytes(policy=policy.default))
            self.rebind_asset(root, "REL-0003")

            self.assertEqual(len(run(root, self.actor_id, self.actor)), 24)

    def test_stale_foundation_provenance_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            card = root / "library/foundations/FOUND-0001.json"
            card.write_bytes(card.read_bytes() + b"\n")
            self.assert_refused_without_results(root)


if __name__ == "__main__":
    unittest.main()
