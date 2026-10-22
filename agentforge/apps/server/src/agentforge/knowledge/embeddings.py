"""Which embedding table serves which embedding model (one table per model and dimension)."""

from dataclasses import dataclass

from agentforge.models.types import ModelRef


@dataclass(frozen=True, slots=True)
class EmbeddingTable:
    name: str  # app.<name>
    dimensions: int
    # Some models are trained with task prefixes and lose quality without them.
    document_prefix: str = ""
    query_prefix: str = ""


_NOMIC = EmbeddingTable("chunk_embeddings_nomic_v15", 768, "search_document: ", "search_query: ")

# Keyed by model name (without provider). Must match migration 0005's EMBEDDING_TABLES.
EMBEDDING_TABLES = {
    "nomic-embed-text:v1.5": _NOMIC,
    "nomic-embed-text:latest": _NOMIC,
}
FAKE_TABLE = EmbeddingTable("chunk_embeddings_fake256", 256)


def table_for(model_ref: str, *, fake: bool = False) -> EmbeddingTable:
    """The embedding table for a model. The fake provider (tests, CI) has its own table."""
    ref = ModelRef.parse(model_ref)
    if fake or ref.provider == "fake":
        return FAKE_TABLE
    table = EMBEDDING_TABLES.get(ref.model)
    if table is None:
        raise ValueError(
            f"No embedding table for {model_ref!r}. Add a migration creating one with the "
            "model's dimensions, then register it in agentforge.knowledge.embeddings."
        )
    return table


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"
