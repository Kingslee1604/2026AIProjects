"""Validation, sandboxed parsing, normalization, chunking, and RRF (no database)."""

import uuid
from pathlib import Path

import pytest

from agentforge.knowledge.chunking import MAX_TOKENS, chunk, embedding_text, normalize
from agentforge.knowledge.parsing import Block, ParseError, parse_file
from agentforge.knowledge.retrieval import or_query, rrf
from agentforge.knowledge.validation import (
    DOCX,
    MARKDOWN,
    PDF,
    UploadRejectedError,
    safe_filename,
    validate_upload,
)

from .documents import make_docx, make_docx_bomb, make_pdf


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# --- Validation (security baseline S5) ------------------------------------------------------


def test_accepts_real_files_by_content(tmp_path: Path) -> None:
    pdf = write(tmp_path, "policy.pdf", make_pdf(["Hello"]))
    assert validate_upload(pdf, "policy.pdf", pdf.stat().st_size).media_type == PDF
    md = write(tmp_path, "notes.md", b"# Title\n\nBody")
    assert validate_upload(md, "notes.md", md.stat().st_size).media_type == MARKDOWN
    docx_path = tmp_path / "doc.docx"
    make_docx(docx_path, "Heading", ["Text"])
    assert validate_upload(docx_path, "doc.docx", docx_path.stat().st_size).media_type == DOCX


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("empty.pdf", b"", "empty"),
        ("fake.pdf", b"MZ\x90\x00 an executable", "not a PDF"),
        ("fake.docx", b"%PDF-1.4 not a zip", "not a Word"),
        ("program.exe", b"MZ", "Unsupported file type"),
        ("binary.txt", b"abc\x00def", "binary data"),
        ("latin1.txt", "café".encode("latin-1"), "UTF-8"),
    ],
)
def test_rejects_mismatched_or_unsupported_files(
    tmp_path: Path, name: str, data: bytes, message: str
) -> None:
    path = write(tmp_path, "upload", data)
    with pytest.raises(UploadRejectedError, match=message):
        validate_upload(path, name, len(data))


def test_rejects_docx_decompression_bomb(tmp_path: Path) -> None:
    bomb = tmp_path / "bomb.docx"
    make_docx_bomb(bomb)
    assert bomb.stat().st_size < 1_000_000  # small on disk...
    with pytest.raises(UploadRejectedError, match="decompression bomb"):
        validate_upload(bomb, "bomb.docx", bomb.stat().st_size)


def test_filenames_are_reduced_to_a_safe_basename() -> None:
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("C:\\Users\\x\\policy?.pdf") == "policy.pdf"
    with pytest.raises(UploadRejectedError):
        safe_filename("../")


# --- Sandboxed parsing ------------------------------------------------------------------------


def test_pdf_pages_are_preserved(tmp_path: Path) -> None:
    pdf = write(
        tmp_path, "p.pdf", make_pdf(["Access logs are kept 90 days.", "Backups run nightly."])
    )
    blocks = parse_file(pdf, PDF)
    assert [(b.page, b.text) for b in blocks] == [
        (1, "Access logs are kept 90 days."),
        (2, "Backups run nightly."),
    ]


def test_docx_headings_and_tables_are_kept(tmp_path: Path) -> None:
    path = tmp_path / "d.docx"
    make_docx(
        path,
        "Encryption Standard",
        ["Data at rest uses AES-256."],
        [["Asset", "Cipher"], ["DB", "AES"]],
    )
    blocks = parse_file(path, DOCX)
    assert blocks[0] == Block(1, "Encryption Standard", 1)
    assert blocks[1].text == "Data at rest uses AES-256."
    assert blocks[2].text.startswith("| Asset | Cipher |")


@pytest.mark.parametrize(
    "data",
    [b"%PDF-1.4\n garbage that is not a pdf", b"%PDF-1.7\n" + b"\xff" * 4096],
    ids=["truncated", "random-bytes"],
)
def test_malformed_pdf_fails_cleanly(tmp_path: Path, data: bytes) -> None:
    with pytest.raises(ParseError):
        parse_file(write(tmp_path, "bad.pdf", data), PDF)


def test_page_limit_is_enforced(tmp_path: Path) -> None:
    huge = write(tmp_path, "huge.pdf", make_pdf(["page"] * 501))
    with pytest.raises(ParseError, match="limit is 500"):
        parse_file(huge, PDF)


def test_scanned_or_empty_pdf_reports_no_text(tmp_path: Path) -> None:
    blank = write(tmp_path, "blank.pdf", make_pdf([""]))
    with pytest.raises(ParseError, match="No text"):
        parse_file(blank, PDF)


# --- Normalization and chunking ----------------------------------------------------------------

POLICY = [
    Block(1, "Information Security Policy", 1),
    Block(1, "Access Control", 2),
    Block(1, " ".join(f"Rule {i} requires review by the security team." for i in range(60)), None),
    Block(2, "Logging", 2),
    Block(2, "Access logs are retained for ninety days. Logs are immutable.", None),
]


def test_chunk_spans_point_exactly_into_the_normalized_text() -> None:
    document = normalize(POLICY)
    chunks = chunk(document)
    assert len(chunks) >= 2
    for c in chunks:
        assert document.text[c.char_start : c.char_end] == c.content
        assert c.token_estimate <= MAX_TOKENS + 100


def test_chunks_follow_sections_and_pages() -> None:
    chunks = chunk(normalize(POLICY))
    logging = [c for c in chunks if "ninety days" in c.content]
    assert logging
    assert logging[-1].section_path == ["Information Security Policy", "Logging"]
    assert logging[-1].page_end == 2


def test_consecutive_chunks_overlap_within_a_section() -> None:
    chunks = chunk(normalize(POLICY))
    first, second = chunks[0], chunks[1]
    assert second.char_start < first.char_end  # carried-over sentences


def test_short_sections_each_get_their_own_chunk() -> None:
    blocks = [
        Block(1, "Security Policy", 1),
        Block(1, "Logging", 2),
        Block(1, "Access logs are kept for ninety days.", None),
        Block(1, "Encryption", 2),
        Block(1, "Data at rest uses AES-256.", None),
    ]
    chunks = chunk(normalize(blocks))
    assert [c.section_path for c in chunks] == [
        ["Security Policy", "Logging"],
        ["Security Policy", "Encryption"],
    ]
    assert chunks[0].content.startswith("Security Policy")  # title stays with first section
    assert embedding_text(chunks[1].section_path, chunks[1].content).startswith(
        "Security Policy > Encryption\n\nEncryption"
    )


def test_page_map_records_where_each_page_starts() -> None:
    document = normalize(POLICY)
    assert [page for page, _ in document.page_map] == [1, 2]
    assert document.text[document.page_map[1][1] :].startswith("Logging")


def test_a_single_huge_sentence_is_split() -> None:
    blob = Block(1, "word " * 3000, None)
    chunks = chunk(normalize([blob]))
    assert len(chunks) > 3
    assert all(c.token_estimate <= MAX_TOKENS for c in chunks)


def test_or_query_matches_any_word_and_cannot_inject_syntax() -> None:
    assert or_query("How long are ACCESS logs retained?") == (
        "how | long | are | access | logs | retained"
    )
    assert or_query("a & b | !c <-> (d):*") == "a | b | c | d"
    assert or_query("?!") == ""


def test_rrf_rewards_agreement_between_channels() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    scores = rrf([[a, b], [b, c]])
    assert max(scores, key=scores.__getitem__) == b
    assert scores[a] == pytest.approx(1 / 61)
