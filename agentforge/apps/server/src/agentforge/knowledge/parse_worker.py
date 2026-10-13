"""Runs inside the sandboxed parse subprocess: `python -m agentforge.knowledge.parse_worker`.

Reads one file, writes JSON blocks to stdout. Imports nothing from the rest of AgentForge and
has no network or database access; the parent applies CPU, memory, file-size, and time limits.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

MAX_PAGES = 500
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _blocks_from_markdown(text: str, page: int = 1) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append({"page": page, "text": "\n".join(paragraph).strip(), "heading": None})
            paragraph.clear()

    for line in text.splitlines():
        match = HEADING.match(line)
        if match:
            flush()
            blocks.append({"page": page, "text": match.group(2), "heading": len(match.group(1))})
        elif not line.strip():
            flush()
        else:
            paragraph.append(line.rstrip())
    flush()
    return [b for b in blocks if b["text"]]


def parse_pdf(path: Path) -> list[dict[str, Any]]:
    import pypdfium2 as pdfium  # pyright: ignore[reportMissingTypeStubs]

    document = pdfium.PdfDocument(str(path))
    try:
        if len(document) > MAX_PAGES:
            raise ValueError(f"The PDF has {len(document)} pages; the limit is {MAX_PAGES}.")
        blocks: list[dict[str, Any]] = []
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            text = textpage.get_text_range().replace("\r\n", "\n").replace("\r", "\n")
            textpage.close()
            page.close()
            for part in re.split(r"\n\s*\n", text):
                # Line breaks inside a PDF paragraph are layout, not content: rejoin words
                # hyphenated at a line end, and turn the remaining breaks into spaces.
                paragraph = re.sub(r"(\w)-\n(\w)", r"\1\2", part.strip())
                paragraph = re.sub(r"\s*\n\s*", " ", paragraph)
                if paragraph:
                    blocks.append({"page": index + 1, "text": paragraph, "heading": None})
        return blocks
    finally:
        document.close()


def parse_docx(path: Path) -> list[dict[str, Any]]:
    import docx
    from docx.text.paragraph import Paragraph

    document = docx.Document(str(path))
    blocks: list[dict[str, Any]] = []
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = item.text.strip()
            if not text:
                continue
            style = (item.style.name if item.style is not None else "") or ""
            level = int(style.rsplit(" ", 1)[-1]) if re.fullmatch(r"Heading [1-6]", style) else None
            blocks.append({"page": 1, "text": text, "heading": level})
        else:  # iter_inner_content yields only paragraphs and tables
            rows = [
                [cell.text.strip().replace("|", "\\|") for cell in row.cells] for row in item.rows
            ]
            if rows:
                lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
                lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
                blocks.append({"page": 1, "text": "\n".join(lines), "heading": None})
    return blocks


def main() -> int:
    path, media_type = Path(sys.argv[1]), sys.argv[2]
    try:
        if media_type == "application/pdf":
            blocks = parse_pdf(path)
        elif media_type.endswith("wordprocessingml.document"):
            blocks = parse_docx(path)
        else:
            blocks = _blocks_from_markdown(path.read_text(encoding="utf-8"))
    except Exception as error:  # reported to the parent as a clean, user-facing failure
        sys.stdout.write(json.dumps({"error": f"{error.__class__.__name__}: {error}"[:500]}))
        return 0
    sys.stdout.write(json.dumps({"blocks": blocks}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
