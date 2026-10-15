"""Gateway data types and the error classes callers branch on."""

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

Role = Literal["system", "user", "assistant"]
CallStatus = Literal["ok", "invalid_output", "timeout", "unavailable", "error"]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class ModelRef:
    """`provider:model`, e.g. `ollama:qwen3:4b` (the model name may contain colons)."""

    provider: str
    model: str

    @classmethod
    def parse(cls, ref: str) -> "ModelRef":
        provider, sep, model = ref.partition(":")
        if not sep or not provider or not model:
            raise ValueError(f"model reference must look like 'provider:model', got {ref!r}")
        return cls(provider, model)

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """What a provider returns for one chat call."""

    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    model_digest: str | None = None


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    model: str  # the model reference that produced the accepted output
    attempts: int
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: float
    model_digest: str | None = None


@dataclass(frozen=True, slots=True)
class CallContext:
    """Who a model call is for, so it is recorded under the right tenant (RLS)."""

    org_id: UUID
    purpose: str  # e.g. "draft", "verify", "plan", "embed", "smoke"
    run_id: UUID | None = None
    step_id: UUID | None = None  # the run step that made the call (trace view)


@dataclass(frozen=True, slots=True)
class CallRecord:
    """One provider call, persisted to app.llm_calls."""

    context: CallContext
    model: str
    status: CallStatus
    attempt: int
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    model_digest: str | None = None
    error_class: str | None = None
    request: list[dict[str, str]] = field(default_factory=lambda: [])
    response: str | None = None
    json_schema: dict[str, Any] | None = None


class ModelError(Exception):
    """Base class. `retryable` tells the run engine whether a later retry may succeed."""

    retryable = False


class ModelTimeoutError(ModelError):
    retryable = True


class ModelUnavailableError(ModelError):
    """Provider unreachable, model not installed, or a 5xx response."""

    retryable = True


class ProviderError(ModelError):
    """The provider rejected the request (4xx); retrying the same request will not help."""


class StructuredOutputError(ModelError):
    """No model in the chain produced output matching the schema, even after repair."""

    retryable = True
