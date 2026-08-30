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
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

from scripts.run_template_technical_validation import ValidationFailed, pdf_pages, run, sha256
from scripts.generate_workbook_recalculation_proof import ProofGenerationFailed, generate, load_object
from scripts.workbook_recalculation import file_sha256 as workbook_file_sha256, workbook_formula_evidence


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


def pdf_with_stream_keyword_text() -> bytes:
    stream_data = b"BT (endstream endobj) Tj ET"
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] /Contents 4 0 R >>",
        f"<< /Length {len(stream_data)} >>\nstream\n".encode("ascii") + stream_data + b"\nendstream",
    )
    payload = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(b"xref\n0 5\n0000000000 65535 f \n")
    for offset in offsets:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        f"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(payload)


def bare_object_pdf() -> bytes:
    newline = b"\n"
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    objects = (
        b"/Type /Catalog /Pages 2 0 R",
        b"/Type /Pages /Count 1 /Kids [3 0 R]",
        b"/Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] /Contents 4 0 R",
        b"/Length 4 stream\njunk\nendstream",
    )
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj".encode("ascii") + newline)
        pdf.extend(body + newline + b"endobj" + newline)
    xref_offset = len(pdf)
    pdf.extend(b"xref\n0 5\n0000000000 65535 f \n")
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n ".encode("ascii") + newline)
    pdf.extend(b"trailer\n<< /Size 5 /Root 1 0 R >>\n")
    pdf.extend(b"startxref\n" + str(xref_offset).encode("ascii") + b"\n%%EOF\n")
    return bytes(pdf)


def literal_string_page_tree_pdf() -> bytes:
    newline = b"\n"
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    objects = (
        b"<< /Dummy (/Type /Catalog /Pages 2 0 R) >>",
        b"<< /Dummy (/Type /Pages /Count 1 /Kids [3 0 R]) >>",
        b"<< /Dummy (/Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] /Contents 4 0 R) >>",
        b"<< /Length 4 >>\nstream\njunk\nendstream",
    )
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj".encode("ascii") + newline)
        pdf.extend(body + newline + b"endobj" + newline)
    xref_offset = len(pdf)
    pdf.extend(b"xref\n0 5\n0000000000 65535 f \n")
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n ".encode("ascii") + newline)
    pdf.extend(b"trailer\n<< /Size 5 /Root 1 0 R >>\n")
    pdf.extend(b"startxref\n" + str(xref_offset).encode("ascii") + b"\n%%EOF\n")
    return bytes(pdf)


def lexically_embedded_object_pdf(container: str) -> bytes:
    """Build an xref graph whose entries point inside an ignored container."""

    fake_objects = (
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] /Contents 4 0 R >> endobj",
        b"4 0 obj << /Length 0 >> stream endstream endobj",
    )
    embedded = b"\n".join(fake_objects) + b"\n"
    payload = bytearray(b"%PDF-1.4\n")
    if container == "comment":
        for fake_object in fake_objects:
            payload.extend(b"% " + fake_object + b"\n")
    elif container == "literal-string":
        payload.extend(b"90 0 obj\n(" + embedded + b")\nendobj\n")
    elif container == "stream":
        stream_data = b"\n".join(fake_objects[:3]) + b"\n4 0 obj << /Length 0 >> stream "
        payload.extend(
            f"90 0 obj\n<< /Length {len(stream_data)} >>\nstream\n".encode("ascii")
            + stream_data
            + b"endstream endobj\n"
        )
    else:  # pragma: no cover - test helper contract
        raise ValueError(container)
    offsets = [payload.find(f"{number} 0 obj".encode("ascii")) for number in range(1, 5)]
    if any(offset < 0 for offset in offsets):  # pragma: no cover - test helper contract
        raise AssertionError("embedded object header missing")
    xref_offset = len(payload)
    payload.extend(b"xref\n0 5\n0000000000 65535 f \n")
    for offset in offsets:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        f"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(payload)


def long_xref_stream_pdf() -> bytes:
    newline = b"\n"
    pdf = bytearray(b"\xef\xbb\xbf%PDF-1.5" + newline)
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
    sparse_ids = tuple(range(100, 10100, 100))
    index = "0 6 " + " ".join(f"{object_id} 1" for object_id in sparse_ids)
    entries = [b"\x00" + (0).to_bytes(4, "big") + (65535).to_bytes(2, "big")]
    entries.extend(b"\x01" + offset.to_bytes(4, "big") + b"\x00\x00" for offset in (*offsets, xref_offset))
    entries.extend(b"\x00\x00\x00\x00\x00\x00\x00" for _ in sparse_ids)
    encoded_entries = b"".join(entries).hex().upper().encode("ascii") + b">"
    pdf.extend(b"5 0 obj<< /W [1 4 2] /Index [" + index.encode("ascii") + b"] ")
    pdf.extend(f"/Size {sparse_ids[-1] + 1} /Root 1 0 R /Length {len(encoded_entries)} ".encode("ascii"))
    pdf.extend(b"/Filter /ASCIIHexDecode /Type /XRef >>" + newline + b"stream" + newline)
    pdf.extend(encoded_entries + newline + b"endstream" + newline + b"endobj" + newline)
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
            for filename in ("render-manifest.json", "terra.json", "terra-proof.txt"):
                evidence_file = source / filename
                if evidence_file.is_file():
                    shutil.copy2(evidence_file, target / evidence_file.name)
            if (source / "render").is_dir():
                shutil.copytree(source / "render", target / "render")
            if (source / "machine-proofs").is_dir():
                shutil.copytree(source / "machine-proofs", target / "machine-proofs")
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

    def bind_secondary_asset(
        self,
        root: Path,
        release_id: str,
        asset: Path,
        media_type: str,
    ) -> None:
        release_path, release, descriptor_path, descriptor, _ = self.release(root, release_id)
        relative = asset.relative_to(root).as_posix()
        digest = sha256(asset)
        descriptor["native_assets"].append(
            {"path": relative, "media_type": media_type, "sha256": digest}
        )
        write_json(descriptor_path, descriptor)
        release["descriptor"]["sha256"] = sha256(descriptor_path)
        release["native_assets"].append({"path": relative, "sha256": digest})
        write_json(release_path, release)

    def write_secondary_email(
        self,
        path: Path,
        *,
        message_id: str,
        omit_header: str | None = None,
        body: str | None = "This contextual supporting note records a routine follow-up.",
    ) -> None:
        message = EmailMessage(policy=policy.default)
        headers = {
            "From": "analyst@example.invalid",
            "To": "manager@example.invalid",
            "Date": "Sun, 30 Aug 2026 10:00:00 -0400",
            "Subject": "Contextual supporting status note",
            "Message-ID": message_id,
        }
        for name, value in headers.items():
            if name != omit_header:
                message[name] = value
        if body is not None:
            message.set_content(body)
        path.write_bytes(message.as_bytes(policy=policy.default))

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

    def test_committed_results_match_current_production_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            generated = run(root, self.actor_id, self.actor, write=True)
            for path in generated:
                committed = ROOT / path.relative_to(root)
                self.assertEqual(path.read_bytes(), committed.read_bytes(), committed.as_posix())

    def test_machine_proof_generator_clears_caches_and_matches_committed_proof(self):
        release = json.loads((ROOT / "library/releases/REL-0001.template.json").read_text(encoding="utf-8"))
        source = ROOT / release["native_assets"][0]["path"]
        source_hash = sha256(source)
        source_evidence = workbook_formula_evidence(source)
        prepared = {}

        def run_subprocess(arguments, **_kwargs):
            if "--version" in arguments:
                return SimpleNamespace(
                    returncode=0,
                    stdout="LibreOffice 26.2.5.2 cd7284b4cbbfeb507e630c1aac019f4157393acb\n",
                    stderr="",
                )
            input_path = Path(arguments[-1])
            prepared.update(workbook_formula_evidence(input_path))
            output_dir = Path(arguments[arguments.index("--outdir") + 1])
            shutil.copy2(source, output_dir / input_path.name)
            return SimpleNamespace(returncode=0, stdout="converted", stderr="")

        with patch("scripts.generate_workbook_recalculation_proof.subprocess.run", side_effect=run_subprocess):
            output, payload = generate(ROOT, "REL-0001", Path("soffice-fixture"))

        self.assertEqual(source_evidence["formula_count"], 99)
        self.assertEqual(prepared["formula_count"], 99)
        self.assertEqual(prepared["cached_formula_count"], 0)
        self.assertEqual(prepared["formula_structure_sha256"], source_evidence["formula_structure_sha256"])
        self.assertEqual(sha256(source), source_hash)
        self.assertEqual(payload, output.read_bytes())

    def test_machine_proof_generator_rejects_descriptor_path_escapes_before_read(self):
        vectors = (
            "../outside.json",
            "C:/outside/descriptor.json",
            r"\\server\share\descriptor.json",
            "library/templates/carrier.json:proof",
        )
        for relative in vectors:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = self.copy_release_inputs(directory)
                release_path = root / "library/releases/REL-0001.template.json"
                release = json.loads(release_path.read_text(encoding="utf-8"))
                if ":" in relative and not relative.startswith("C:"):
                    descriptor_path = root / release["descriptor"]["path"]
                    (root / relative.split(":", 1)[0]).write_bytes(b"carrier")
                    (root / relative).write_bytes(descriptor_path.read_bytes())
                release["descriptor"]["path"] = relative
                write_json(release_path, release)
                with patch(
                    "scripts.generate_workbook_recalculation_proof.load_object",
                    wraps=load_object,
                ) as reader:
                    with self.assertRaisesRegex(ProofGenerationFailed, "repository-relative path|escapes repository root"):
                        generate(root, "REL-0001", Path("soffice-fixture"))
                self.assertEqual([call.args[0] for call in reader.call_args_list], [release_path])

    def test_machine_proof_generator_rejects_workbook_path_escapes_before_read(self):
        vectors = (
            "../outside.xlsx",
            "C:/outside/workbook.xlsx",
            r"\\server\share\workbook.xlsx",
            "library/templates/carrier.xlsx:proof",
        )
        for relative in vectors:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = self.copy_release_inputs(directory)
                release_path, release, descriptor_path, descriptor, source = self.release(root, "REL-0001")
                if ":" in relative and not relative.startswith("C:"):
                    (root / relative.split(":", 1)[0]).write_bytes(b"carrier")
                    (root / relative).write_bytes(source.read_bytes())
                descriptor["native_assets"][0]["path"] = relative
                write_json(descriptor_path, descriptor)
                release["descriptor"]["sha256"] = sha256(descriptor_path)
                write_json(release_path, release)
                with patch(
                    "scripts.generate_workbook_recalculation_proof.file_sha256",
                    wraps=workbook_file_sha256,
                ) as hasher, patch(
                    "scripts.generate_workbook_recalculation_proof.workbook_formula_evidence",
                    wraps=workbook_formula_evidence,
                ) as formula_reader:
                    with self.assertRaisesRegex(ProofGenerationFailed, "repository-relative path|escapes repository root"):
                        generate(root, "REL-0001", Path("soffice-fixture"))
                self.assertEqual([call.args[0] for call in hasher.call_args_list], [descriptor_path])
                formula_reader.assert_not_called()

    def test_machine_proof_generator_rejects_resolved_workbook_escape_before_read(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside_directory:
            root = self.copy_release_inputs(directory)
            release_path, release, descriptor_path, descriptor, _ = self.release(root, "REL-0001")
            relative = "library/templates/workbook-link.xlsx"
            descriptor["native_assets"][0]["path"] = relative
            write_json(descriptor_path, descriptor)
            release["descriptor"]["sha256"] = sha256(descriptor_path)
            write_json(release_path, release)
            candidate = root / relative
            outside = Path(outside_directory).resolve() / "outside.xlsx"
            outside.write_bytes(b"outside bytes must not be read")
            original_resolve = Path.resolve

            def simulate_link(path, *args, **kwargs):
                if original_resolve(path, *args, **kwargs) == original_resolve(candidate):
                    return outside
                return original_resolve(path, *args, **kwargs)

            with patch("pathlib.Path.resolve", autospec=True, side_effect=simulate_link), patch(
                "scripts.generate_workbook_recalculation_proof.file_sha256",
                wraps=workbook_file_sha256,
            ) as hasher, patch(
                "scripts.generate_workbook_recalculation_proof.workbook_formula_evidence",
                wraps=workbook_formula_evidence,
            ) as formula_reader:
                with self.assertRaisesRegex(ProofGenerationFailed, "escapes repository root"):
                    generate(root, "REL-0001", Path("soffice-fixture"))
            self.assertEqual([call.args[0] for call in hasher.call_args_list], [descriptor_path])
            formula_reader.assert_not_called()

    def test_machine_proof_generator_rejects_resolved_descriptor_escape_before_read(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside_directory:
            root = self.copy_release_inputs(directory)
            release_path = root / "library/releases/REL-0001.template.json"
            release = json.loads(release_path.read_text(encoding="utf-8"))
            relative = "library/templates/descriptor-link.json"
            release["descriptor"]["path"] = relative
            write_json(release_path, release)
            candidate = root / relative
            outside = Path(outside_directory).resolve() / "outside.json"
            write_json(outside, {"outside": "must not be read"})
            original_resolve = Path.resolve

            def simulate_link(path, *args, **kwargs):
                if original_resolve(path, *args, **kwargs) == original_resolve(candidate):
                    return outside
                return original_resolve(path, *args, **kwargs)

            with patch("pathlib.Path.resolve", autospec=True, side_effect=simulate_link), patch(
                "scripts.generate_workbook_recalculation_proof.load_object",
                wraps=load_object,
            ) as reader:
                with self.assertRaisesRegex(ProofGenerationFailed, "escapes repository root"):
                    generate(root, "REL-0001", Path("soffice-fixture"))
            self.assertEqual([call.args[0] for call in reader.call_args_list], [release_path])

    def test_invalid_render_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            png = root / "evidence/template-releases/REL-0002/render/internal-controller-memo-page-3.png"
            png.write_bytes(b"not a PNG")
            self.assert_refused_without_results(root)

    def test_marker_only_pdf_render_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, descriptor, _ = self.release(root, "REL-0002")
            pdf = root / descriptor["render_contract"]["expected_pdf_path"]
            pdf.write_bytes(b"%PDF-1.7\n" + (b"/Type /Page\n" * 5) + b"%%EOF\n")
            manifest_path = root / descriptor["render_contract"]["evidence_manifest"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            rendered = manifest["templates"][descriptor["template_id"]]["rendered_outputs"]
            next(item for item in rendered if item["path"] == descriptor["render_contract"]["expected_pdf_path"])["sha256"] = sha256(pdf)
            write_json(manifest_path, manifest)
            self.assert_refused_without_results(root)

    def test_pdf_render_with_bare_page_tree_objects_is_refused_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "bare-page-tree.pdf"
            pdf.write_bytes(bare_object_pdf())
            with self.assertRaisesRegex(ValidationFailed, "structurally valid PDF"):
                pdf_pages(pdf)

    def test_pdf_render_with_page_tree_tokens_only_in_strings_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "literal-string-page-tree.pdf"
            pdf.write_bytes(literal_string_page_tree_pdf())
            with self.assertRaisesRegex(ValidationFailed, "structurally valid PDF"):
                pdf_pages(pdf)

    def test_pdf_stream_payload_may_contain_endstream_endobj_text(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "stream-keyword-text.pdf"
            pdf.write_bytes(pdf_with_stream_keyword_text())
            self.assertEqual(pdf_pages(pdf), 1)

    def test_pdf_xref_cannot_point_to_objects_embedded_in_ignored_containers(self):
        for container in ("comment", "literal-string", "stream"):
            with self.subTest(container=container), tempfile.TemporaryDirectory() as directory:
                pdf = Path(directory) / f"embedded-{container}.pdf"
                pdf.write_bytes(lexically_embedded_object_pdf(container))
                with self.assertRaisesRegex(
                    ValidationFailed,
                    "xref entry does not point to a top-level indirect object",
                ):
                    pdf_pages(pdf)

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

    def test_tampered_formula_with_stale_nonempty_cache_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0001")

            def replace_formula_but_keep_cache(tree):
                formula_cell = next(
                    cell
                    for cell in tree.findall(f".//{{{MAIN}}}c")
                    if cell.find(f"{{{MAIN}}}f") is not None
                    and cell.find(f"{{{MAIN}}}v") is not None
                    and (cell.find(f"{{{MAIN}}}v").text or "").strip()
                )
                formula_cell.find(f"{{{MAIN}}}f").text = "1/0"

            self.mutate_zip_member(asset, "xl/worksheets/sheet8.xml", replace_formula_but_keep_cache)
            self.rebind_asset(root, "REL-0001")
            with self.assertRaisesRegex(ValidationFailed, "machine recalculation proof"):
                run(root, self.actor_id, self.actor, write=True)
            self.assertEqual(
                list(root.glob("evidence/template-releases/REL-*/technical-results/*.json")),
                [],
            )

    def test_benign_formula_mutation_with_stale_nonerror_cache_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0001")

            def replace_formula_but_keep_cache(tree):
                formula_cell = next(
                    cell
                    for cell in tree.findall(f".//{{{MAIN}}}c")
                    if cell.find(f"{{{MAIN}}}f") is not None
                    and cell.find(f"{{{MAIN}}}v") is not None
                    and (cell.find(f"{{{MAIN}}}v").text or "").strip()
                )
                formula_cell.find(f"{{{MAIN}}}f").text = "2+2"

            self.mutate_zip_member(asset, "xl/worksheets/sheet8.xml", replace_formula_but_keep_cache)
            self.rebind_asset(root, "REL-0001")
            with self.assertRaisesRegex(ValidationFailed, "machine recalculation proof"):
                run(root, self.actor_id, self.actor, write=True)
            self.assertEqual(
                list(root.glob("evidence/template-releases/REL-*/technical-results/*.json")),
                [],
            )

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

    def test_secondary_eml_without_required_headers_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, primary = self.release(root, "REL-0003")
            secondary = primary.with_name("supporting-status-note.eml")
            message = EmailMessage(policy=policy.default)
            message["From"] = "analyst@example.invalid"
            message["Message-ID"] = "<supporting-status-note@example.invalid>"
            message["Subject"] = "Supporting status note"
            message.set_content("This contextual note records a routine follow-up.")
            secondary.write_bytes(message.as_bytes(policy=policy.default))
            self.bind_secondary_asset(root, "REL-0003", secondary, "message/rfc822")
            self.assert_refused_without_results(root)

    def test_non_mixed_descriptor_rejects_heterogeneous_secondary_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, primary = self.release(root, "REL-0003")
            source = root / "library/templates/TMPL-0002/1.0.0/internal-controller-memo.docx"
            secondary = primary.with_name("sanitized-supporting-memo.docx")
            with zipfile.ZipFile(source) as package:
                members = {
                    info.filename: package.read(info.filename)
                    for info in package.infolist()
                }
            for name, payload in members.items():
                if name.casefold().endswith((".xml", ".rels", ".vml")):
                    members[name] = payload.replace(b"{", b"[").replace(b"}", b"]")
            with zipfile.ZipFile(secondary, "w") as package:
                for name, payload in members.items():
                    package.writestr(name, payload)
            self.bind_secondary_asset(
                root,
                "REL-0003",
                secondary,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            self.assert_refused_without_results(root)

    def test_xlsx_descriptor_rejects_second_workbook(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, primary = self.release(root, "REL-0001")
            secondary = primary.with_name("uncontracted-secondary.xlsx")
            shutil.copy2(primary, secondary)
            self.bind_secondary_asset(
                root,
                "REL-0001",
                secondary,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            with self.assertRaisesRegex(ValidationFailed, "xlsx descriptor must bind exactly one"):
                run(root, self.actor_id, self.actor, write=True)

    def test_docx_descriptor_rejects_second_document(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, primary = self.release(root, "REL-0002")
            secondary = primary.with_name("uncontracted-secondary.docx")
            shutil.copy2(primary, secondary)
            self.bind_secondary_asset(
                root,
                "REL-0002",
                secondary,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            with self.assertRaisesRegex(ValidationFailed, "docx descriptor must bind exactly one"):
                run(root, self.actor_id, self.actor, write=True)

    def test_secondary_eml_without_date_or_message_id_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, primary = self.release(root, "REL-0003")
            secondary = primary.with_name("undated-supporting-note.eml")
            message = EmailMessage(policy=policy.default)
            message["From"] = "analyst@example.invalid"
            message["To"] = "manager@example.invalid"
            message["Subject"] = "Supporting status note"
            message.set_content("This contextual supporting note records a routine follow-up.")
            secondary.write_bytes(message.as_bytes(policy=policy.default))
            self.bind_secondary_asset(root, "REL-0003", secondary, "message/rfc822")
            with self.assertRaisesRegex(ValidationFailed, "lacks required headers"):
                run(root, self.actor_id, self.actor, write=True)

    def test_each_required_secondary_eml_header_is_independently_enforced(self):
        for header in ("From", "To", "Date", "Subject", "Message-ID"):
            with self.subTest(header=header), tempfile.TemporaryDirectory() as directory:
                root = self.copy_release_inputs(directory)
                _, _, _, _, primary = self.release(root, "REL-0003")
                secondary = primary.with_name(f"missing-{header.casefold()}-supporting-note.eml")
                body = "This contextual supporting note records a routine follow-up."
                if header == "From":
                    body += "\nFrom: quoted-history@example.invalid"
                self.write_secondary_email(
                    secondary,
                    message_id=f"<missing-{header.casefold()}@example.invalid>",
                    omit_header=header,
                    body=body,
                )
                self.bind_secondary_asset(root, "REL-0003", secondary, "message/rfc822")
                with self.assertRaisesRegex(ValidationFailed, "lacks required headers or message content"):
                    run(root, self.actor_id, self.actor, write=True)
                self.assertEqual(
                    list(root.glob("evidence/template-releases/REL-*/technical-results/*.json")),
                    [],
                )

    def test_secondary_eml_with_empty_body_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, primary = self.release(root, "REL-0003")
            secondary = primary.with_name("empty-supporting-note.eml")
            self.write_secondary_email(
                secondary,
                message_id="<empty-supporting-note@example.invalid>",
                body=None,
            )
            self.bind_secondary_asset(root, "REL-0003", secondary, "message/rfc822")
            with self.assertRaisesRegex(ValidationFailed, "lacks required headers or message content"):
                run(root, self.actor_id, self.actor, write=True)

    def test_valid_bounded_secondary_eml_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, primary = self.release(root, "REL-0003")
            secondary = primary.with_name("bounded-supporting-note.eml")
            self.write_secondary_email(
                secondary,
                message_id="<bounded-supporting-note@example.invalid>",
            )
            self.bind_secondary_asset(root, "REL-0003", secondary, "message/rfc822")
            self.assertEqual(len(run(root, self.actor_id, self.actor)), 24)

    def test_secondary_eml_without_authority_marker_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, primary = self.release(root, "REL-0003")
            secondary = primary.with_name("routine-status-note.eml")
            message = EmailMessage(policy=policy.default)
            message["From"] = "analyst@example.invalid"
            message["To"] = "manager@example.invalid"
            message["Message-ID"] = "<routine-status-note@example.invalid>"
            message["Subject"] = "Routine status note"
            message.set_content("The follow-up meeting remains on the calendar.")
            secondary.write_bytes(message.as_bytes(policy=policy.default))
            self.bind_secondary_asset(root, "REL-0003", secondary, "message/rfc822")
            self.assert_refused_without_results(root)

    def test_duplicate_message_ids_across_bound_eml_assets_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, primary = self.release(root, "REL-0003")
            secondary = primary.with_name("duplicate-thread.eml")
            shutil.copy2(primary, secondary)
            self.bind_secondary_asset(root, "REL-0003", secondary, "message/rfc822")
            with self.assertRaisesRegex(ValidationFailed, "duplicate Message-ID"):
                run(root, self.actor_id, self.actor, write=True)
            self.assertEqual(
                list(root.glob("evidence/template-releases/REL-*/technical-results/*.json")),
                [],
            )

    def test_combined_eml_footprint_uses_every_bound_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, primary = self.release(root, "REL-0003")
            secondary = primary.with_name("additional-thread.eml")
            sequence = iter(range(1, 100))

            def unique_message_id(_match):
                return f"Message-ID: <additional-{next(sequence)}@example.invalid>".encode("ascii")

            payload = re.sub(
                rb"(?im)^Message-ID:\s*\S+",
                unique_message_id,
                primary.read_bytes(),
            )
            secondary.write_bytes(payload + b"\r\nFrom: retained-copy@example.invalid\r\n")
            self.bind_secondary_asset(root, "REL-0003", secondary, "message/rfc822")
            with self.assertRaisesRegex(ValidationFailed, "combined email footprint"):
                run(root, self.actor_id, self.actor, write=True)
            self.assertEqual(
                list(root.glob("evidence/template-releases/REL-*/technical-results/*.json")),
                [],
            )

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
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline), tempfile.TemporaryDirectory() as directory:
                root = self.copy_release_inputs(directory)
                _, _, _, _, asset = self.release(root, "REL-0003")
                message = BytesParser(policy=policy.default).parsebytes(asset.read_bytes())
                attachment = EmailMessage(policy=policy.default)
                attachment["Content-Type"] = 'text/plain; charset="iso-8859-1"'
                attachment["Content-Disposition"] = 'attachment; filename="review.txt"'
                attachment["Content-Transfer-Encoding"] = "base64"
                attachment.set_payload(base64.b64encode(minimal_compact_pdf(newline)).decode("ascii"))
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

    def test_long_xref_stream_dictionary_mislabeled_as_text_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_release_inputs(directory)
            _, _, _, _, asset = self.release(root, "REL-0003")
            message = BytesParser(policy=policy.default).parsebytes(asset.read_bytes())
            pdf = long_xref_stream_pdf()
            xref_offset = int(re.search(rb"startxref\s+(\d+)", pdf).group(1))
            self.assertGreater(pdf.find(b"/Type /XRef", xref_offset) - xref_offset, 512)
            attachment = EmailMessage(policy=policy.default)
            attachment["Content-Type"] = 'text/plain; charset="iso-8859-1"'
            attachment["Content-Disposition"] = 'attachment; filename="xref-review.txt"'
            attachment["Content-Transfer-Encoding"] = "base64"
            attachment.set_payload(base64.b64encode(pdf).decode("ascii"))
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
