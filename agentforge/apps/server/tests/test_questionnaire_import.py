"""Questionnaire import (R1-31): header detection, XLSX and CSV, limits, and rejections."""

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from agentforge.knowledge.validation import UploadRejectedError
from agentforge.questionnaires.importer import (
    MAX_CELL_CHARS,
    MAX_ITEMS,
    ColumnMapping,
    detect_columns,
    parse_questionnaire,
)
from tools.demo_corpus import ROOT as DEMO_ROOT
from tools.demo_corpus import _write_xlsx  # pyright: ignore[reportPrivateUsage]

MB = 1024 * 1024


def xlsx(path: Path, rows: list[list[Any]], *, title: str = "Sheet1") -> Path:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = title
    for row in rows:
        sheet.append(row)
    workbook.save(str(path))
    return path


def parse(path: Path, **kwargs: Any):
    return parse_questionnaire(path, path.name, path.stat().st_size, max_bytes=25 * MB, **kwargs)


def test_the_northwind_questionnaire_imports_40_items(tmp_path: Path) -> None:
    source = json.loads(
        (DEMO_ROOT / "questionnaire/northwind-questionnaire.json").read_text("utf-8")
    )
    path = tmp_path / "northwind-questionnaire.xlsx"
    _write_xlsx(source["questions"], path, source["title"])
    parsed = parse(path)
    assert parsed.format == "xlsx"
    assert parsed.sheet == "Questionnaire"
    assert parsed.header_row == 1
    assert parsed.columns.letters() == {"question": "C", "id": "A", "section": "B", "answer": "D"}
    assert len(parsed.items) == 40
    first = parsed.items[0]
    assert first.row == 2
    assert first.question == source["questions"][0]["question"]
    assert first.external_id == source["questions"][0]["id"]
    assert first.section == source["questions"][0]["section"]


def test_header_below_a_title_and_other_column_names(tmp_path: Path) -> None:
    path = xlsx(
        tmp_path / "vendor.xlsx",
        [
            ["Acme vendor security assessment 2026"],
            [],
            ["Ref", "Domain", "Requirement", "Vendor Response", "Evidence"],
            ["1.1", "Access", "Do you enforce MFA?", None, None],
            [None, None, None, None, None],  # blank rows are skipped
            ["1.2", "Access", "Are accounts reviewed quarterly?", None, None],
        ],
    )
    parsed = parse(path)
    assert parsed.header_row == 3
    assert parsed.columns == ColumnMapping(question=2, id=0, section=1, answer=3)
    assert [(i.row, i.external_id) for i in parsed.items] == [(4, "1.1"), (6, "1.2")]


def test_question_beats_description_wherever_it_sits() -> None:
    mapping = detect_columns(["ID", "Description", "Question", "Answer"])
    assert mapping is not None
    assert mapping.question == 2


def test_no_answer_column_means_the_export_adds_one(tmp_path: Path) -> None:
    parsed = parse(xlsx(tmp_path / "q.xlsx", [["Question"], ["Do you encrypt backups?"]]))
    assert parsed.columns.answer is None
    assert parsed.columns.letters()["answer"] is None


def test_existing_answers_produce_a_warning(tmp_path: Path) -> None:
    parsed = parse(
        xlsx(tmp_path / "q.xlsx", [["Question", "Answer"], ["Do you encrypt backups?", "Yes"]])
    )
    assert parsed.warnings == ["Row 2 already has an answer; it will be replaced."]


def test_numbers_and_formulas_are_read_as_values(tmp_path: Path) -> None:
    parsed = parse(xlsx(tmp_path / "q.xlsx", [["ID", "Question"], [7, "=1+1"], [8.0, "Plain?"]]))
    # Formulas are never evaluated: a formula cell without a cached value reads as empty.
    assert [(i.external_id, i.question) for i in parsed.items] == [("8", "Plain?")]


def test_csv_with_semicolons_and_bom(tmp_path: Path) -> None:
    path = tmp_path / "q.csv"
    path.write_bytes(
        "﻿ID;Section;Question\nA1;Access;Is MFA required?\nA2;Access;Is SSO supported?\n".encode()
    )
    parsed = parse(path)
    assert parsed.format == "csv"
    assert [i.question for i in parsed.items] == ["Is MFA required?", "Is SSO supported?"]
    assert parsed.items[0].row == 2


def test_columns_can_be_chosen_by_hand(tmp_path: Path) -> None:
    path = xlsx(tmp_path / "q.xlsx", [["A", "B", "C"], ["x1", "What is your RTO?", "4 h"]])
    with pytest.raises(UploadRejectedError, match="question column"):
        parse(path)
    # Without a recognizable header, the user's mapping cannot help either: a header row is
    # still required, so this documents the error rather than a silent guess.
    path = xlsx(
        tmp_path / "q2.xlsx", [["Ref", "Text", "Question"], ["x1", "What is your RTO?", "-"]]
    )
    parsed = parse(path, mapping=ColumnMapping(question=1, id=0))
    assert [i.question for i in parsed.items] == ["What is your RTO?"]


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("q.pdf", b"%PDF-1.7", "Excel"),
        ("q.xlsx", b"not a zip at all", "not an Excel workbook"),
        ("q.csv", b"Question\n\x00binary", "binary"),
        ("q.csv", "Question\nCaf\xe9?\n".encode("latin-1"), "UTF-8"),
        ("q.csv", b"", "empty"),
    ],
)
def test_bad_files_are_rejected_with_a_reason(
    tmp_path: Path, name: str, content: bytes, message: str
) -> None:
    path = tmp_path / name
    path.write_bytes(content)
    with pytest.raises(UploadRejectedError, match=message):
        parse(path)


def test_macro_enabled_workbooks_are_refused(tmp_path: Path) -> None:
    path = xlsx(tmp_path / "q.xlsx", [["Question"], ["Do you patch?"]])
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("xl/vbaProject.bin", b"\x00macro")
    with pytest.raises(UploadRejectedError, match="Macro"):
        parse(path)


def test_limits(tmp_path: Path) -> None:
    too_many = xlsx(
        tmp_path / "many.xlsx", [["Question"]] + [[f"Q{i}?"] for i in range(MAX_ITEMS + 1)]
    )
    with pytest.raises(UploadRejectedError, match=f"more than {MAX_ITEMS} questions"):
        parse(too_many)
    long_cell = xlsx(tmp_path / "long.xlsx", [["Question"], ["x" * (MAX_CELL_CHARS + 1)]])
    with pytest.raises(UploadRejectedError, match="Row 2"):
        parse(long_cell)
    small = xlsx(tmp_path / "ok.xlsx", [["Question"], ["Fine?"]])
    with pytest.raises(UploadRejectedError, match="larger than"):
        parse_questionnaire(small, small.name, small.stat().st_size, max_bytes=10)


def test_header_only_file_has_no_questions(tmp_path: Path) -> None:
    with pytest.raises(UploadRejectedError, match="No questions"):
        parse(xlsx(tmp_path / "q.xlsx", [["ID", "Question", "Answer"]]))


def test_hidden_sheets_are_ignored(tmp_path: Path) -> None:
    workbook = Workbook()
    hidden = workbook.active
    assert hidden is not None
    hidden.title = "Internal"
    hidden.append(["Question"])
    hidden.append(["Secret internal question?"])
    hidden.sheet_state = "hidden"
    visible = workbook.create_sheet("Questions")
    visible.append(["Question"])
    visible.append(["Public question?"])
    workbook.active = 1
    path = tmp_path / "q.xlsx"
    workbook.save(str(path))
    parsed = parse(path)
    assert parsed.sheet == "Questions"
    assert [i.question for i in parsed.items] == ["Public question?"]


def test_the_stored_file_needs_no_extension(tmp_path: Path) -> None:
    """Regression: uploads and blobs are stored without an extension; openpyxl must not judge
    the format by the path's name (the content has already been validated)."""
    source = xlsx(tmp_path / "q.xlsx", [["Question"], ["Do you encrypt backups?"]])
    stored = tmp_path / "upload-3f9a"
    stored.write_bytes(source.read_bytes())
    parsed = parse_questionnaire(stored, "q.xlsx", stored.stat().st_size, max_bytes=25 * MB)
    assert [i.question for i in parsed.items] == ["Do you encrypt backups?"]
