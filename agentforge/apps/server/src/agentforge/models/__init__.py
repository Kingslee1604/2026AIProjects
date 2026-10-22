"""Model gateway (architecture §8.2, ADR-5): one interface for chat, structured output, and
embeddings over Ollama, OpenAI-compatible servers, and a deterministic fake provider."""

from agentforge.models.gateway import ModelGateway, build_gateway
from agentforge.models.types import (
    CallContext,
    GenerationResult,
    Message,
    ModelError,
    ModelTimeoutError,
    ModelUnavailableError,
    StructuredOutputError,
)

__all__ = [
    "CallContext",
    "GenerationResult",
    "Message",
    "ModelError",
    "ModelGateway",
    "ModelTimeoutError",
    "ModelUnavailableError",
    "StructuredOutputError",
    "build_gateway",
]
