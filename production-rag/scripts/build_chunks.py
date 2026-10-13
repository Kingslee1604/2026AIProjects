"""
Runs the structure-aware chunker over every parsed document in data/processed/
(produced by notebooks/01_docling_parse_corpus.ipynb) and writes one JSONL file
of retrieval-ready chunks to data/chunks/chunks.jsonl.

Usage:
    python scripts/build_chunks.py
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.chunk.chunker import chunk_document  # noqa: E402

PROCESSED_DIR = REPO_ROOT / "data" / "processed"
CHUNKS_PATH = REPO_ROOT / "data" / "chunks" / "chunks.jsonl"


def main() -> int:
    doc_paths = sorted(PROCESSED_DIR.glob("*.json"))
    if not doc_paths:
        print(f"No parsed documents found in {PROCESSED_DIR}. "
              f"Run notebooks/01_docling_parse_corpus.ipynb first and unzip its "
              f"output there.")
        return 1

    CHUNKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    total_chunks = 0
    with CHUNKS_PATH.open("w", encoding="utf-8") as out:
        for doc_path in doc_paths:
            doc = json.loads(doc_path.read_text(encoding="utf-8"))
            chunks = chunk_document(doc)
            for c in chunks:
                record = c.to_dict()
                record["doc_title"] = doc.get("title", doc["doc_id"])
                record["doc_license"] = doc.get("license")
                record["doc_resolved_url"] = doc.get("resolved_url")
                out.write(json.dumps(record) + "\n")
            total_chunks += len(chunks)
            print(f"{doc['doc_id']}: {len(chunks)} chunks")

    print(f"\nWrote {total_chunks} chunks from {len(doc_paths)} documents to "
          f"{CHUNKS_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
