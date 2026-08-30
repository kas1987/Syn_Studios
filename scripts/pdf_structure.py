"""Fail-closed structural inspection for the classic-xref PDF subset.

The release PDFs currently use classic cross-reference tables.  This module
parses the catalog and page tree with a small PDF lexer so names hidden inside
literal strings, comments, hex strings, or streams cannot masquerade as
dictionary keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


PDF_HEADER = re.compile(rb"%PDF-(?:1\.[0-7]|2\.0)(?:\r\n|\r|\n)")
PDF_TRAILER = re.compile(rb"startxref\s+(\d+)\s+%%EOF\s*\Z")
WHITESPACE = b"\x00\x09\x0a\x0c\x0d\x20"
DELIMITERS = b"()<>[]{}/%"


@dataclass(frozen=True)
class PdfName:
    value: bytes


@dataclass(frozen=True)
class PdfReference:
    number: int
    generation: int


@dataclass(frozen=True)
class Token:
    kind: str
    value: Any
    start: int
    end: int


class Lexer:
    def __init__(self, payload: bytes, offset: int = 0):
        self.payload = payload
        self.offset = offset
        self._buffer: list[Token] = []

    def _skip_ignored(self) -> None:
        length = len(self.payload)
        while self.offset < length:
            byte = self.payload[self.offset]
            if byte in WHITESPACE:
                self.offset += 1
                continue
            if byte == ord("%"):
                newline = self.payload.find(b"\n", self.offset + 1)
                carriage = self.payload.find(b"\r", self.offset + 1)
                endings = [value for value in (newline, carriage) if value >= 0]
                self.offset = min(endings) if endings else length
                continue
            break

    def _literal_string(self, start: int) -> Token:
        depth = 1
        escaped = False
        position = start + 1
        while position < len(self.payload):
            byte = self.payload[position]
            if escaped:
                if byte == ord("\r") and position + 1 < len(self.payload) and self.payload[position + 1] == ord("\n"):
                    position += 1
                escaped = False
            elif byte == ord("\\"):
                escaped = True
            elif byte == ord("("):
                depth += 1
            elif byte == ord(")"):
                depth -= 1
                if depth == 0:
                    self.offset = position + 1
                    return Token("string", self.payload[start + 1:position], start, self.offset)
            position += 1
        raise ValueError("unterminated PDF literal string")

    def _name(self, start: int) -> Token:
        position = start + 1
        while position < len(self.payload) and self.payload[position] not in WHITESPACE + DELIMITERS:
            position += 1
        raw = self.payload[start + 1:position]

        def unescape(match: re.Match[bytes]) -> bytes:
            return bytes([int(match.group(1), 16)])

        value = re.sub(rb"#([0-9A-Fa-f]{2})", unescape, raw)
        self.offset = position
        return Token("name", PdfName(value), start, position)

    def _hex_string(self, start: int) -> Token:
        end = self.payload.find(b">", start + 1)
        if end < 0:
            raise ValueError("unterminated PDF hex string")
        self.offset = end + 1
        return Token("string", self.payload[start + 1:end], start, self.offset)

    def _next(self) -> Token:
        self._skip_ignored()
        start = self.offset
        if start >= len(self.payload):
            return Token("eof", None, start, start)
        if self.payload.startswith(b"<<", start):
            self.offset += 2
            return Token("dict_start", b"<<", start, self.offset)
        if self.payload.startswith(b">>", start):
            self.offset += 2
            return Token("dict_end", b">>", start, self.offset)
        byte = self.payload[start]
        if byte == ord("["):
            self.offset += 1
            return Token("array_start", b"[", start, self.offset)
        if byte == ord("]"):
            self.offset += 1
            return Token("array_end", b"]", start, self.offset)
        if byte == ord("("):
            return self._literal_string(start)
        if byte == ord("<"):
            return self._hex_string(start)
        if byte == ord("/"):
            return self._name(start)
        position = start
        while position < len(self.payload) and self.payload[position] not in WHITESPACE + DELIMITERS:
            position += 1
        if position == start:
            raise ValueError(f"unexpected PDF delimiter at byte {start}")
        raw = self.payload[start:position]
        self.offset = position
        if re.fullmatch(rb"[+-]?\d+", raw):
            return Token("integer", int(raw), start, position)
        if re.fullmatch(rb"[+-]?(?:\d+\.\d*|\.\d+)", raw):
            return Token("number", float(raw), start, position)
        return Token("keyword", raw, start, position)

    def peek(self, distance: int = 0) -> Token:
        while len(self._buffer) <= distance:
            self._buffer.append(self._next())
        return self._buffer[distance]

    def pop(self) -> Token:
        token = self.peek()
        self._buffer.pop(0)
        return token


def parse_value(lexer: Lexer) -> Any:
    token = lexer.pop()
    if token.kind == "dict_start":
        result: dict[bytes, Any] = {}
        while lexer.peek().kind != "dict_end":
            key = lexer.pop()
            if key.kind != "name":
                raise ValueError("PDF dictionary key is not a name")
            name = key.value.value
            if name in result:
                raise ValueError("PDF dictionary contains a duplicate key")
            result[name] = parse_value(lexer)
        lexer.pop()
        return result
    if token.kind == "array_start":
        result = []
        while lexer.peek().kind != "array_end":
            if lexer.peek().kind == "eof":
                raise ValueError("unterminated PDF array")
            result.append(parse_value(lexer))
        lexer.pop()
        return result
    if token.kind == "integer":
        if lexer.peek().kind == "integer" and lexer.peek(1).kind == "keyword" and lexer.peek(1).value == b"R":
            generation = lexer.pop().value
            lexer.pop()
            return PdfReference(token.value, generation)
        return token.value
    if token.kind in {"number", "name", "string"}:
        return token.value
    if token.kind == "keyword" and token.value in {b"true", b"false", b"null"}:
        return {b"true": True, b"false": False, b"null": None}[token.value]
    raise ValueError("unsupported or malformed PDF value")


def parse_indirect_object(
    payload: bytes,
    entries: dict[tuple[int, int], int],
    reference: PdfReference,
) -> tuple[Any, Token]:
    key = (reference.number, reference.generation)
    offset = entries.get(key)
    if offset is None:
        raise ValueError("page tree references a missing object")
    header = re.match(rb"(\d+)\s+(\d+)\s+obj\b", payload[offset:])
    if header is None or (int(header.group(1)), int(header.group(2))) != key:
        raise ValueError("object header is malformed")
    lexer = Lexer(payload, offset + header.end())
    value = parse_value(lexer)
    return value, lexer.pop()


def parse_xref(payload: bytes, offset: int) -> tuple[dict[tuple[int, int], int], PdfReference]:
    section = payload[offset:]
    if not section.startswith(b"xref"):
        raise ValueError("startxref does not point to a classic xref table")
    trailer_line = re.search(rb"(?m)^trailer[\t ]*\r?$", section)
    if trailer_line is None:
        raise ValueError("xref table has no trailer")
    lines = section[len(b"xref"):trailer_line.start()].splitlines()
    entries: dict[tuple[int, int], int] = {}
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line:
            continue
        header = re.fullmatch(rb"(\d+)\s+(\d+)", line)
        if header is None:
            raise ValueError("malformed xref subsection")
        first, count = (int(value) for value in header.groups())
        if count < 1 or index + count > len(lines):
            raise ValueError("truncated xref subsection")
        for number in range(first, first + count):
            entry = re.fullmatch(rb"(\d{10})\s+(\d{5})\s+([fn])", lines[index].strip())
            index += 1
            if entry is None:
                raise ValueError("malformed xref entry")
            object_offset, generation, state = entry.groups()
            if state == b"n":
                key = (number, int(generation))
                if key in entries:
                    raise ValueError("duplicate in-use xref entry")
                entries[key] = int(object_offset)
    trailer_lexer = Lexer(section, trailer_line.end())
    trailer = parse_value(trailer_lexer)
    if not isinstance(trailer, dict) or not isinstance(trailer.get(b"Root"), PdfReference):
        raise ValueError("trailer has no catalog root")
    root = trailer[b"Root"]
    if (root.number, root.generation) not in entries:
        raise ValueError("catalog root is absent from xref")
    for (number, generation), object_offset in entries.items():
        if object_offset < 0 or object_offset >= offset:
            raise ValueError("xref object offset is outside the object section")
        header = re.match(rb"(\d+)\s+(\d+)\s+obj\b", payload[object_offset:])
        if header is None or (int(header.group(1)), int(header.group(2))) != (number, generation):
            raise ValueError("xref entry does not point to its object header")
    return entries, root


def _name(value: Any) -> bytes | None:
    return value.value if isinstance(value, PdfName) else None


def _content_references(value: Any) -> list[PdfReference]:
    if isinstance(value, PdfReference):
        return [value]
    if isinstance(value, list) and value and all(isinstance(item, PdfReference) for item in value):
        return value
    raise ValueError("page lacks a valid Contents reference")


def inspect_pdf(payload: bytes) -> dict[str, Any]:
    """Return parsed page structure or raise ``ValueError``."""

    header = PDF_HEADER.search(payload[:1024])
    trailer = PDF_TRAILER.search(payload)
    if header is None or trailer is None:
        raise ValueError("missing PDF header or final startxref trailer")
    xref_offset = int(trailer.group(1))
    if xref_offset < header.end() or xref_offset >= trailer.start():
        raise ValueError("startxref offset is outside the PDF body")
    entries, root = parse_xref(payload, xref_offset)
    catalog, catalog_tail = parse_indirect_object(payload, entries, root)
    if catalog_tail.kind != "keyword" or catalog_tail.value != b"endobj":
        raise ValueError("catalog object has trailing content")
    if not isinstance(catalog, dict) or _name(catalog.get(b"Type")) != b"Catalog":
        raise ValueError("root object is not a catalog dictionary")
    pages_root = catalog.get(b"Pages")
    if not isinstance(pages_root, PdfReference):
        raise ValueError("catalog lacks a Pages reference")

    visited: set[PdfReference] = set()
    page_records: list[dict[str, Any]] = []

    def visit(reference: PdfReference, parent: PdfReference | None, inherited_media_box: Any = None) -> int:
        if reference in visited:
            raise ValueError("page tree contains a cycle or duplicate child")
        visited.add(reference)
        body, tail = parse_indirect_object(payload, entries, reference)
        if tail.kind != "keyword" or tail.value != b"endobj":
            raise ValueError("page-tree object has trailing content")
        if not isinstance(body, dict):
            raise ValueError("page-tree object is not a PDF dictionary")
        body_type = _name(body.get(b"Type"))
        media_box = body.get(b"MediaBox", inherited_media_box)
        if body_type == b"Pages":
            count, kids = body.get(b"Count"), body.get(b"Kids")
            if not isinstance(count, int) or count < 1 or not isinstance(kids, list) or not kids:
                raise ValueError("pages node lacks a valid Count or Kids array")
            if not all(isinstance(child, PdfReference) for child in kids):
                raise ValueError("pages node has malformed Kids")
            actual = sum(visit(child, reference, media_box) for child in kids)
            if actual != count:
                raise ValueError("pages node Count does not match its page tree")
            return actual
        if body_type != b"Page":
            raise ValueError("page tree child is neither Page nor Pages")
        if parent is None or body.get(b"Parent") != parent:
            raise ValueError("page Parent does not match its containing Pages node")
        if not isinstance(media_box, list) or len(media_box) != 4 or not all(isinstance(item, (int, float)) for item in media_box):
            raise ValueError("page lacks a numeric MediaBox")
        content_refs = _content_references(body.get(b"Contents"))
        for content_ref in content_refs:
            content, content_tail = parse_indirect_object(payload, entries, content_ref)
            if not isinstance(content, dict) or content_tail.kind != "keyword" or content_tail.value != b"stream":
                raise ValueError("page Contents does not reference a stream object")
            endstream = payload.find(b"endstream", content_tail.end)
            endobj = payload.find(b"endobj", content_tail.end)
            if endstream < content_tail.end or endobj < endstream:
                raise ValueError("page content stream is not terminated")
        page_records.append({"media_box": media_box, "content_references": content_refs})
        return 1

    page_count = visit(pages_root, None)
    return {"page_count": page_count, "pages": page_records}
