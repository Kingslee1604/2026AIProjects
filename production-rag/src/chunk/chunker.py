"""
Structure-aware chunker.

Consumes the per-document JSON produced by notebooks/01_docling_parse_corpus.ipynb
(typed elements: text / table / figure, each with page + section metadata) and
produces retrieval-ready chunks.

Rules (see plan): tables and figures are atomic -- never split mid-table or
mid-figure. Narrative text is grouped into chunks up to a character budget,
breaking at element (paragraph) boundaries and whenever the section heading
changes, so a chunk never straddles two sections.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Approximate chars-per-token ~4 for English; keeps this dependency-free.
TEXT_CHUNK_CHAR_BUDGET = 1200
TABLE_MAX_ROWS_PER_CHUNK = 40


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    chunk_type: str  # "text" | "table" | "figure"
    page: int | None
    section: str | None
    text: str
    citation_quote: str
    source_element_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "chunk_type": self.chunk_type,
            "page": self.page,
            "section": self.section,
            "text": self.text,
            "citation_quote": self.citation_quote,
            "source_element_ids": self.source_element_ids,
        }


def _truncate(text: str, max_len: int = 240) -> str:
    text = " ".join(text.split())
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"


def _split_table_markdown(markdown: str, max_rows: int) -> list[str]:
    """Splits a markdown table into row-groups of at most max_rows data rows,
    repeating the header (first two lines: header + separator) in each part."""
    lines = markdown.splitlines()
    if len(lines) <= 2:
        return [markdown]
    header, sep, *data_rows = lines
    if len(data_rows) <= max_rows:
        return [markdown]
    parts = []
    for i in range(0, len(data_rows), max_rows):
        group = data_rows[i : i + max_rows]
        parts.append("\n".join([header, sep, *group]))
    return parts


def _chunk_table(doc_id: str, element: dict, chunk_index: int) -> list[Chunk]:
    markdown = element.get("markdown", "") or ""
    caption = element.get("caption")
    parts = _split_table_markdown(markdown, TABLE_MAX_ROWS_PER_CHUNK)
    chunks = []
    for part_i, part_md in enumerate(parts):
        prefix = f"Table ({caption}):\n" if caption else "Table:\n"
        text = prefix + part_md
        suffix = f"-part{part_i + 1}" if len(parts) > 1 else ""
        chunks.append(Chunk(
            chunk_id=f"{doc_id}__chunk-{chunk_index}{suffix}",
            doc_id=doc_id,
            chunk_type="table",
            page=element.get("page"),
            section=element.get("section"),
            text=text,
            citation_quote=_truncate(caption or part_md),
            source_element_ids=[element["element_id"]],
        ))
    return chunks


def _chunk_figure(doc_id: str, element: dict, chunk_index: int) -> Chunk:
    caption = element.get("caption_original") or ""
    description = element.get("vlm_description") or ""
    text = "Figure"
    if caption:
        text += f" caption: {caption}"
    if description:
        text += f"\nImage description: {description}"
    return Chunk(
        chunk_id=f"{doc_id}__chunk-{chunk_index}",
        doc_id=doc_id,
        chunk_type="figure",
        page=element.get("page"),
        section=element.get("section"),
        text=text,
        citation_quote=_truncate(caption or description or "figure"),
        source_element_ids=[element["element_id"]],
    )


def _flush_text_group(doc_id: str, group: list[dict], chunk_index: int) -> Chunk | None:
    if not group:
        return None
    combined = "\n\n".join(e["text"] for e in group)
    return Chunk(
        chunk_id=f"{doc_id}__chunk-{chunk_index}",
        doc_id=doc_id,
        chunk_type="text",
        page=group[0].get("page"),
        section=group[0].get("section"),
        text=combined,
        citation_quote=_truncate(combined),
        source_element_ids=[e["element_id"] for e in group],
    )


def chunk_document(doc: dict) -> list[Chunk]:
    """doc is the parsed-document dict: {"doc_id": ..., "elements": [...]}"""
    doc_id = doc["doc_id"]
    chunks: list[Chunk] = []
    chunk_index = 0
    text_group: list[dict] = []
    text_group_chars = 0

    def flush():
        nonlocal chunk_index, text_group, text_group_chars
        c = _flush_text_group(doc_id, text_group, chunk_index)
        if c is not None:
            chunks.append(c)
            chunk_index += 1
        text_group = []
        text_group_chars = 0

    prev_section = None
    for element in doc.get("elements", []):
        etype = element.get("type")

        if etype == "text":
            section_changed = prev_section is not None and element.get("section") != prev_section
            would_overflow = text_group_chars + len(element["text"]) > TEXT_CHUNK_CHAR_BUDGET
            if text_group and (section_changed or would_overflow):
                flush()
            text_group.append(element)
            text_group_chars += len(element["text"])
            prev_section = element.get("section")

        elif etype == "table":
            flush()
            chunks.extend(_chunk_table(doc_id, element, chunk_index))
            chunk_index = len(chunks)  # a large table may emit >1 chunk
            prev_section = element.get("section")

        elif etype == "figure":
            flush()
            chunks.append(_chunk_figure(doc_id, element, chunk_index))
            chunk_index += 1
            prev_section = element.get("section")

    flush()
    return chunks


def chunk_documents(docs: list[dict]) -> list[Chunk]:
    all_chunks: list[Chunk] = []
    for doc in docs:
        all_chunks.extend(chunk_document(doc))
    return all_chunks
