import json
from pathlib import Path

import pytest

from src.chunk.chunker import chunk_document

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_doc.json"


@pytest.fixture()
def sample_doc() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_produces_one_chunk_per_table_never_split_across_chunks(sample_doc):
    chunks = chunk_document(sample_doc)
    table_chunks = [c for c in chunks if c.chunk_type == "table"]

    assert len(table_chunks) == 1
    # every row of the source table must appear in the single table chunk
    for drug in ["Metoprolol succinate", "Carvedilol", "Bisoprolol"]:
        assert drug in table_chunks[0].text


def test_table_chunk_is_atomic_not_merged_with_surrounding_text(sample_doc):
    chunks = chunk_document(sample_doc)
    for c in chunks:
        if c.chunk_type == "table":
            assert "Beta-blockers are a first-line therapy" not in c.text
            assert "Patients should be monitored" not in c.text


def test_large_table_is_split_into_row_groups_with_header_repeated():
    header = "| Drug | Dose |\n| --- | --- |"
    rows = [f"| Drug{i} | {i} mg |" for i in range(100)]
    big_table_doc = {
        "doc_id": "big-table-doc",
        "elements": [{
            "element_id": "big-table-doc-table-1",
            "type": "table",
            "page": 1,
            "section": "Dosing",
            "caption": "Big table",
            "markdown": "\n".join([header, *rows]),
        }],
    }
    chunks = chunk_document(big_table_doc)
    assert len(chunks) > 1
    for c in chunks:
        assert c.text.startswith("Table (Big table):\n| Drug | Dose |")


def test_figure_chunk_is_atomic_and_carries_vlm_description(sample_doc):
    chunks = chunk_document(sample_doc)
    figure_chunks = [c for c in chunks if c.chunk_type == "figure"]

    assert len(figure_chunks) == 1
    assert "Titration flowchart" in figure_chunks[0].text
    assert "gradual up-titration" in figure_chunks[0].text
    assert figure_chunks[0].section == "Monitoring"
    assert figure_chunks[0].page == 3


def test_text_chunks_are_grouped_and_split_on_section_change(sample_doc):
    chunks = chunk_document(sample_doc)
    text_chunks = [c for c in chunks if c.chunk_type == "text"]

    # the two Introduction paragraphs should be merged into one chunk...
    intro_chunks = [c for c in text_chunks if c.section == "Introduction"]
    assert len(intro_chunks) == 1
    assert "first-line therapy" in intro_chunks[0].text
    assert "dosing recommendations" in intro_chunks[0].text

    # ...and the Monitoring paragraph must NOT be merged into the Introduction chunk
    monitoring_chunks = [c for c in text_chunks if c.section == "Monitoring"]
    assert len(monitoring_chunks) == 1
    assert "bradycardia" in monitoring_chunks[0].text
    assert "first-line therapy" not in monitoring_chunks[0].text


def test_chunk_ids_are_unique_and_stable_within_a_document(sample_doc):
    chunks = chunk_document(sample_doc)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))
    assert all(cid.startswith("fixture-doc-1__chunk-") for cid in ids)


def test_every_chunk_traces_back_to_source_element_ids(sample_doc):
    chunks = chunk_document(sample_doc)
    for c in chunks:
        assert len(c.source_element_ids) >= 1
