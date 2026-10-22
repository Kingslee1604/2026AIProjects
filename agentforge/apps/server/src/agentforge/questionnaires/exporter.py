"""Write answers back into a copy of the customer's questionnaire (R1-35).

The original file is never modified: its blob is copied, answers go into the remembered answer
column (or a new "Answer" column after the last used one), and sources go into a "Sources"
column next to it. Cell text is written as plain values (never formulas: a value starting
with = + - @ is prefixed with an apostrophe-like guard so spreadsheet apps do not evaluate it).
"""

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.styles import Alignment, Font

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
MAX_CELL = 32_000  # Excel's limit is 32,767 characters


@dataclass(frozen=True, slots=True)
class ExportRow:
    row: int  # 1-based sheet row (CSV: line number)
    answer: str
    sources: str


def safe_cell(value: str) -> str:
    """Stop formula injection (CSV/Excel): text that looks like a formula is kept as text."""
    text = value[:MAX_CELL]
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def export_xlsx(
    source: Path,
    target: Path,
    *,
    sheet: str | None,
    header_row: int,
    answer_column: int | None,
    rows: Sequence[ExportRow],
) -> None:
    with source.open("rb") as handle:
        workbook = load_workbook(handle)  # full mode keeps formatting, formulas, other sheets
    worksheet = workbook[sheet] if sheet else workbook.active
    assert worksheet is not None  # noqa: S101 - a workbook always has a sheet

    def cell_at(row: int, column: int) -> Cell | None:
        """A writable cell (0-based column), or None inside a merged range (left untouched)."""
        cell = worksheet.cell(row=row, column=column + 1)
        return cell if isinstance(cell, Cell) else None

    # New columns go after the last used one: the customer's own columns (e.g. "Notes") are
    # never overwritten. worksheet.max_column is also the zero-based index of the next column.
    if answer_column is None:
        answer_column = worksheet.max_column
        header = cell_at(header_row, answer_column)
        if header is not None:
            header.value = "Answer"
            header.font = Font(bold=True)
    sources_column = worksheet.max_column
    title = cell_at(header_row, sources_column)
    if title is not None:
        title.value = "Sources"
        title.font = Font(bold=True)
    for row in rows:
        for column, value in ((answer_column, row.answer), (sources_column, row.sources)):
            cell = cell_at(row.row, column)
            if cell is not None:
                cell.value = safe_cell(value)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    workbook.save(str(target))


def export_csv(
    source: Path,
    target: Path,
    *,
    header_row: int,
    answer_column: int | None,
    rows: Sequence[ExportRow],
) -> None:
    content = source.read_bytes().decode("utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(content[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    table = [list(r) for r in csv.reader(io.StringIO(content), dialect)]
    width = max((len(r) for r in table), default=0)
    if answer_column is None:  # new columns go after the last used one, as in XLSX
        answer_column = width
        width += 1
    sources_column = width
    by_row = {r.row: r for r in rows}
    out = io.StringIO()
    writer = csv.writer(out, dialect)
    for number, cells in enumerate(table, start=1):
        cells = cells + [""] * (sources_column + 1 - len(cells))
        if number == header_row:
            cells[answer_column] = cells[answer_column] or "Answer"
            cells[sources_column] = "Sources"
        elif number in by_row:
            cells[answer_column] = safe_cell(by_row[number].answer)
            cells[sources_column] = safe_cell(by_row[number].sources)
        writer.writerow(cells)
    target.write_text(out.getvalue(), encoding="utf-8")
