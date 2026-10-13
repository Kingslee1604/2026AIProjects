"""Shared helper: builds (or loads) the Qdrant index the eval harness queries
against. 'fixture' corpus is the small hand-verified set in tests/fixtures/,
used to prove the harness itself is correct. 'real' corpus is whatever
scripts/build_chunks.py + scripts/build_index.py produced from the actual
downloaded/parsed documents."""

from __future__ import annotations

import json
from pathlib import Path

from src.chunk.chunker import chunk_document
from src.index.hybrid_index import build_index, get_client
from src.retrieve.retriever import HybridRetriever

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
REAL_CHUNKS_PATH = REPO_ROOT / "data" / "chunks" / "chunks.jsonl"
REAL_QDRANT_PATH = REPO_ROOT / "data" / "qdrant_local"
FIXTURE_QDRANT_PATH = REPO_ROOT / "data" / "qdrant_eval_fixture"


def load_fixture_chunks() -> list[dict]:
    chunks = []
    for doc_path in sorted(FIXTURES_DIR.glob("sample_doc*.json")):
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        chunks.extend(c.to_dict() for c in chunk_document(doc))
    return chunks


def get_retriever(corpus: str = "fixture", rebuild: bool = False) -> HybridRetriever:
    if corpus == "fixture":
        client = get_client(FIXTURE_QDRANT_PATH)
        if rebuild or not client.collection_exists("cardiology_chunks"):
            build_index(client, load_fixture_chunks())
        return HybridRetriever(client)

    if corpus == "real":
        if not REAL_CHUNKS_PATH.exists():
            raise FileNotFoundError(
                f"{REAL_CHUNKS_PATH} not found. Run scripts/build_chunks.py "
                f"(after the Colab parsing notebook) first."
            )
        client = get_client(REAL_QDRANT_PATH)
        if rebuild or not client.collection_exists("cardiology_chunks"):
            chunks = [json.loads(l) for l in REAL_CHUNKS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
            build_index(client, chunks)
        return HybridRetriever(client)

    raise ValueError(f"Unknown corpus: {corpus}")
