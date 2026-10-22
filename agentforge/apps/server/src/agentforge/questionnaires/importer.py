"""Questionnaire import: find the header row and columns, then read one item per question.

Real questionnaires differ in layout, so the header row is detected from common column names
(and can be overridden). The answer column is remembered so the export (R1-35) can write into
the customer's own file. Files are validated by content and size before any parser opens them,
values are read without evaluating formulas, and macro-enabled workbooks are refused.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from agentforge.knowledge.validation import UploadRejectedError, check_zip_container, safe_filename

MAX_ITEMS = 500
MAX_CELL_CHARS = 4000
MAX_SCAN_ROWS = 5000  # rows read per sheet (blank rows included)
MAX_COLUMNS = 60
HEADER_SEARCH_ROWS = 30

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV = "text/csv"
EXTENSIONS: dict[str, str] = {".xlsx": XLSX, ".csv": CSV}

Role = Literal["question", "id", "section", "answer"]

# Normalized header text -> role. Checked in order; "question" is required.
_HEADER_NAMES: dict[Role, tuple[str, ...]] = {
    "question": (
        "question",
        "questions",
        "question text",
        "requirement",
        "control",
        "control question",
        "query",
        "item",
        "description",
    ),
    "id": ("id", "ref", "reference", "no", "number", "#", "question id", "control id", "item id"),
    "section": ("section", "category", "domain", "area", "topic", "group"),
    "answer": (
        "answer",
        "response",
        "vendor response",
        "supplier response",
        "your answer",
        "your response",
        "comments",
    ),
}
_SPACES = re.compile(r"[\s_]+")


def _norm(value: object) -> str:
    return _SPACES.sub(" ", str(value or "")).strip().strip(":*").strip().casefold()


@dataclass(frozen=True, slots=True)
class ParsedItem:
    row: int  # 1-based row in the sheet (CSV: line number)
    question: str
    external_id: str | None
    section: str | None


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    """Zero-based column indexes; `answer` None means the export appends a new column."""

    question: int
    id: int | None = None
    section: int | None = None
    answer: int | None = None

    def letters(self) -> dict[str, str | None]:
        def letter(index: int | None) -> str | None:
            return None if index is None else get_column_letter(index + 1)

        return {
            "question": letter(self.question),
            "id": letter(self.id),
            "section": letter(self.section),
            "answer": letter(self.answer),
        }


@dataclass(frozen=True, slots=True)
class ParsedQuestionnaire:
    format: Literal["xlsx", "csv"]
    media_type: str
    filename: str
    sheet: str | None
    header_row: int
    columns: ColumnMapping
    headers: list[str]
    items: list[ParsedItem]
    warnings: list[str] = field(default_factory=lambda: [])


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def detect_columns(header: list[str]) -> ColumnMapping | None:
    """Map a header row to roles; None if it has no recognizable question column."""
    normalized = [_norm(cell) for cell in header]
    found: dict[Role, int] = {}
    for role, names in _HEADER_NAMES.items():
        # Names are in priority order: "Question" beats "Description" wherever they sit.
        for name in names:
            index = next(
                (
                    i
                    for i, cell in enumerate(normalized)
                    if cell == name and i not in found.values()
                ),
                None,
            )
            if index is not None:
                found[role] = index
                break
    if "question" not in found:
        return None
    return ColumnMapping(
        question=found["question"],
        id=found.get("id"),
        section=found.get("section"),
        answer=found.get("answer"),
    )


def _items_from_rows(
    rows: list[tuple[int, list[str]]], mapping: ColumnMapping
) -> tuple[list[ParsedItem], list[str]]:
    items: list[ParsedItem] = []
    warnings: list[str] = []
    for row_number, cells in rows:

        def at(index: int | None, cells: list[str] = cells) -> str:
            return cells[index] if index is not None and index < len(cells) else ""

        question = at(mapping.question)
        if not question:
            continue
        for value in (question, at(mapping.id), at(mapping.section)):
            if len(value) > MAX_CELL_CHARS:
                raise UploadRejectedError(
                    f"Row {row_number} has a cell longer than {MAX_CELL_CHARS} characters."
                )
        if mapping.answer is not None and at(mapping.answer):
            warnings.append(f"Row {row_number} already has an answer; it will be replaced.")
        items.append(
            ParsedItem(
                row=row_number,
                question=question,
                external_id=at(mapping.id) or None,
                section=at(mapping.section) or None,
            )
        )
        if len(items) > MAX_ITEMS:
            raise UploadRejectedError(
                f"The questionnaire has more than {MAX_ITEMS} questions. Split it into parts."
            )
    if not items:
        raise UploadRejectedError("No questions were found below the header row.")
    return items, warnings[:20]


def _find_header(rows: list[tuple[int, list[str]]]) -> tuple[int, ColumnMapping, list[str]]:
    for row_number, cells in rows[:HEADER_SEARCH_ROWS]:
        mapping = detect_columns(cells)
        if mapping is not None:
            return row_number, mapping, cells
    raise UploadRejectedError(
        "Could not find the question column. Give it a header such as 'Question' or "
        "'Requirement' in one of the first rows."
    )


def _read_xlsx(path: Path) -> tuple[str, list[tuple[int, list[str]]]]:
    names = check_zip_container(
        path,
        required={"[Content_Types].xml", "xl/workbook.xml"},
        kind="Excel (.xlsx) workbook",
    )
    if any(name.lower().endswith("vbaproject.bin") for name in names):
        raise UploadRejectedError("Macro-enabled workbooks are not accepted. Save it as .xlsx.")
    # An open file, not a path: openpyxl would otherwise judge the format by the file name's
    # extension, and uploads and blobs are stored without one (the content was checked above).
    handle = path.open("rb")
    try:
        workbook = load_workbook(handle, read_only=True, data_only=True)
    except Exception as error:  # openpyxl raises many types for damaged files
        handle.close()
        raise UploadRejectedError("The Excel file could not be read.") from error
    try:
        # The first visible sheet that has a question header wins; the active sheet first.
        sheets = [workbook.active, *workbook.worksheets]
        seen: set[str] = set()
        for sheet in sheets:
            if (
                sheet is None
                or sheet.title in seen
                or getattr(sheet, "sheet_state", "") != "visible"
            ):
                continue
            seen.add(sheet.title)
            rows: list[tuple[int, list[str]]] = []
            for number, values in enumerate(
                sheet.iter_rows(max_col=MAX_COLUMNS, values_only=True), start=1
            ):
                if number > MAX_SCAN_ROWS:
                    raise UploadRejectedError(
                        f"The sheet '{sheet.title}' has more than {MAX_SCAN_ROWS} rows."
                    )
                rows.append((number, [_cell_text(v) for v in values]))
            if any(detect_columns(cells) for _, cells in rows[:HEADER_SEARCH_ROWS]):
                return sheet.title, rows
        raise UploadRejectedError(
            "Could not find the question column in any sheet. Give it a header such as "
            "'Question' or 'Requirement'."
        )
    finally:
        workbook.close()
        handle.close()


def _read_csv(path: Path) -> list[tuple[int, list[str]]]:
    data = path.read_bytes()
    if b"\x00" in data:
        raise UploadRejectedError("The CSV file contains binary data.")
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise UploadRejectedError("CSV files must be UTF-8 encoded.") from error
    try:
        dialect = csv.Sniffer().sniff(content[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows: list[tuple[int, list[str]]] = []
    for number, cells in enumerate(csv.reader(io.StringIO(content), dialect), start=1):
        if number > MAX_SCAN_ROWS:
            raise UploadRejectedError(f"The CSV file has more than {MAX_SCAN_ROWS} rows.")
        rows.append((number, [cell.strip() for cell in cells[:MAX_COLUMNS]]))
    return rows


def parse_questionnaire(
    path: Path, filename: str, size: int, *, max_bytes: int, mapping: ColumnMapping | None = None
) -> ParsedQuestionnaire:
    name = safe_filename(filename)
    if size == 0:
        raise UploadRejectedError("The file is empty.")
    if size > max_bytes:
        raise UploadRejectedError(f"The file is larger than {max_bytes // (1024 * 1024)} MB.")
    extension = Path(name).suffix.lower()
    media_type = EXTENSIONS.get(extension)
    if media_type is None:
        raise UploadRejectedError("Upload an Excel (.xlsx) or CSV (.csv) questionnaire.")
    with path.open("rb") as handle:
        head = handle.read(4)
    sheet: str | None = None
    if media_type == XLSX:
        if head != b"PK\x03\x04":
            raise UploadRejectedError("The file has an .xlsx name but is not an Excel workbook.")
        sheet, rows = _read_xlsx(path)
    else:
        rows = _read_csv(path)
    header_row, detected, headers = _find_header(rows)
    columns = mapping or detected
    body = [(number, cells) for number, cells in rows if number > header_row]
    items, warnings = _items_from_rows(body, columns)
    return ParsedQuestionnaire(
        format="xlsx" if media_type == XLSX else "csv",
        media_type=media_type,
        filename=name,
        sheet=sheet,
        header_row=header_row,
        columns=columns,
        headers=headers,
        items=items,
        warnings=warnings,
    )
