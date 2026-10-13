"""
Builds and loads the local hybrid (dense + BM25 sparse) Qdrant collection.

Runs entirely on CPU via FastEmbed (ONNX runtime) -- no GPU, no server process;
Qdrant runs in local/embedded mode against an on-disk path, so the whole index
lives inside the repo's data/ directory.
"""

from __future__ import annotations

from pathlib import Path

from qdrant_client import QdrantClient, models

DENSE_MODEL = "BAAI/bge-small-en-v1.5"
SPARSE_MODEL = "Qdrant/bm25"
COLLECTION_NAME = "cardiology_chunks"


def get_client(qdrant_path: Path) -> QdrantClient:
    client = QdrantClient(path=str(qdrant_path))
    client.set_model(DENSE_MODEL)
    client.set_sparse_model(SPARSE_MODEL)
    return client


def vector_field_names(client: QdrantClient) -> tuple[str, str]:
    """Returns (dense_field_name, sparse_field_name) as assigned by FastEmbed."""
    dense_name = next(iter(client.get_fastembed_vector_params()))
    sparse_name = next(iter(client.get_fastembed_sparse_vector_params()))
    return dense_name, sparse_name


def build_index(client: QdrantClient, chunks: list[dict], batch_size: int = 64) -> None:
    """chunks: list of chunk dicts (as produced by scripts/build_chunks.py),
    each must have at least 'chunk_id' and 'text'."""
    dense_name, sparse_name = vector_field_names(client)

    if client.collection_exists(COLLECTION_NAME):
        client.delete_collection(COLLECTION_NAME)
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=client.get_fastembed_vector_params(),
        sparse_vectors_config=client.get_fastembed_sparse_vector_params(),
    )

    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        points = [
            models.PointStruct(
                id=start + i,
                vector={
                    dense_name: models.Document(text=c["text"], model=DENSE_MODEL),
                    sparse_name: models.Document(text=c["text"], model=SPARSE_MODEL),
                },
                payload=c,
            )
            for i, c in enumerate(batch)
        ]
        client.upsert(collection_name=COLLECTION_NAME, points=points)
