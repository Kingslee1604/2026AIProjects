"""
Builds the local hybrid Qdrant index from data/chunks/chunks.jsonl
(produced by scripts/build_chunks.py).

Usage:
    python scripts/build_index.py
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.index.hybrid_index import build_index, get_client  # noqa: E402

CHUNKS_PATH = REPO_ROOT / "data" / "chunks" / "chunks.jsonl"
QDRANT_PATH = REPO_ROOT / "data" / "qdrant_local"


def main() -> int:
    if not CHUNKS_PATH.exists():
        print(f"{CHUNKS_PATH} not found. Run scripts/build_chunks.py first.")
        return 1

    chunks = [json.loads(line) for line in CHUNKS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"Loaded {len(chunks)} chunks")

    client = get_client(QDRANT_PATH)
    build_index(client, chunks)
    print(f"Indexed {len(chunks)} chunks into {QDRANT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
