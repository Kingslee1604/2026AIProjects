"""The model gateway: structured output with validation, one repair attempt, ordered fallback,
and a record of every provider call (architecture §8.1-8.2)."""

import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from agentforge.core.settings import Settings
from agentforge.models.providers import (
    FakeProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    Provider,
)
from agentforge.models.types import (
    CallContext,
    CallRecord,
    CallStatus,
    GenerationResult,
    Message,
    ModelError,
    ModelRef,
    ModelTimeoutError,
    ModelUnavailableError,
    ProviderResponse,
    StructuredOutputError,
)

T = TypeVar("T", bound=BaseModel)
Recorder = Callable[[CallRecord], Awaitable[None]]

REPAIR_INSTRUCTION = (
    "Your previous reply was not valid for the required JSON schema. Error: {error}\n"
    "Reply again with only a JSON object that matches the schema. No prose, no code fences."
)


async def _discard(_: CallRecord) -> None:
    return None


class ModelGateway:
    def __init__(
        self,
        providers: Mapping[str, Provider],
        *,
        recorder: Recorder = _discard,
        timeout: float = 120.0,
        force_provider: str | None = None,
    ) -> None:
        self._providers = dict(providers)
        self._record = recorder
        self._timeout = timeout
        # MODEL_PROVIDER=fake routes every model reference to the fake provider.
        self._force_provider = force_provider

    def provider(self, name: str) -> Provider | None:
        """A configured provider by name (tests use it to script the fake provider)."""
        return self._providers.get(name)

    def _resolve(self, ref: str) -> tuple[Provider, str, str]:
        parsed = ModelRef.parse(ref)
        provider_name = self._force_provider or parsed.provider
        provider = self._providers.get(provider_name)
        if provider is None:
            raise ModelUnavailableError(f"provider {provider_name!r} is not configured")
        return provider, parsed.model, f"{provider_name}:{parsed.model}"

    async def _call(
        self,
        context: CallContext,
        ref: str,
        messages: Sequence[Message],
        *,
        schema: dict[str, Any] | None,
        attempt: int,
        temperature: float,
        max_tokens: int | None,
        validate: Callable[[str], object] | None = None,
    ) -> tuple[ProviderResponse, float]:
        """One provider call, recorded exactly once. `validate` runs before recording, so an
        output that fails validation is recorded as `invalid_output` (and re-raised)."""
        provider, model, label = self._resolve(ref)
        started = time.perf_counter()
        status: CallStatus = "ok"
        error_class: str | None = None
        response: ProviderResponse | None = None
        try:
            response = await provider.chat(
                model,
                messages,
                json_schema=schema,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=self._timeout,
            )
            if validate is not None:
                validate(response.text)
            return response, (time.perf_counter() - started) * 1000
        except ValidationError:
            status, error_class = "invalid_output", "validation"
            raise
        except ModelTimeoutError:
            status, error_class = "timeout", "timeout"
            raise
        except ModelUnavailableError:
            status, error_class = "unavailable", "unavailable"
            raise
        except ModelError as error:
            status, error_class = "error", error.__class__.__name__
            raise
        finally:
            await self._record(
                CallRecord(
                    context=context,
                    model=label,
                    status=status,
                    attempt=attempt,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                    prompt_tokens=response.prompt_tokens if response else None,
                    completion_tokens=response.completion_tokens if response else None,
                    model_digest=response.model_digest if response else None,
                    error_class=error_class,
                    request=[{"role": m.role, "content": m.content} for m in messages],
                    response=response.text if response else None,
                    json_schema=schema,
                )
            )

    async def generate(
        self,
        context: CallContext,
        messages: Sequence[Message],
        *,
        model: str,
        fallbacks: Sequence[str] = (),
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> GenerationResult:
        """Free-text generation, falling back to the next model on timeout or outage."""
        last_error: ModelError | None = None
        for attempt, ref in enumerate([model, *fallbacks], start=1):
            try:
                response, latency = await self._call(
                    context,
                    ref,
                    messages,
                    schema=None,
                    attempt=attempt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except (ModelTimeoutError, ModelUnavailableError) as error:
                last_error = error
                continue
            return GenerationResult(
                text=response.text,
                model=self._resolve(ref)[2],
                attempts=attempt,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                latency_ms=latency,
                model_digest=response.model_digest,
            )
        assert last_error is not None  # noqa: S101 - the loop ran at least once
        raise last_error

    async def generate_structured(
        self,
        context: CallContext,
        messages: Sequence[Message],
        output: type[T],
        *,
        model: str,
        fallbacks: Sequence[str] = (),
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> tuple[T, GenerationResult]:
        """Output validated against `output`. Per model: one try plus one repair attempt with
        the validation error; then the next model in the fallback chain."""
        schema = output.model_json_schema()
        attempt = 0
        failures: list[str] = []
        for ref in [model, *fallbacks]:
            conversation = list(messages)
            for _ in range(2):  # first try, then one repair
                attempt += 1
                parsed_holder: list[T] = []
                replies: list[str] = []

                def validate(
                    text: str, replies: list[str] = replies, parsed_holder: list[T] = parsed_holder
                ) -> None:
                    replies.append(text)
                    parsed_holder.append(output.model_validate_json(_strip_fences(text)))

                try:
                    response, latency = await self._call(
                        context,
                        ref,
                        conversation,
                        schema=schema,
                        attempt=attempt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        validate=validate,
                    )
                except (ModelTimeoutError, ModelUnavailableError) as error:
                    failures.append(f"{ref}: {error.__class__.__name__}")
                    break  # an outage will not be fixed by a repair prompt: next model
                except ValidationError as error:
                    detail = _short_error(error)
                    failures.append(f"{ref}: invalid output ({detail})")
                    conversation = [
                        *conversation,
                        Message("assistant", replies[-1] if replies else ""),
                        Message("user", REPAIR_INSTRUCTION.format(error=detail)),
                    ]
                    continue
                parsed = parsed_holder[0]
                result = GenerationResult(
                    text=response.text,
                    model=self._resolve(ref)[2],
                    attempts=attempt,
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    latency_ms=latency,
                    model_digest=response.model_digest,
                )
                return parsed, result
        raise StructuredOutputError("; ".join(failures) or "no model configured")

    async def embed(
        self, context: CallContext, texts: Sequence[str], *, model: str
    ) -> list[list[float]]:
        provider, model_name, label = self._resolve(model)
        started = time.perf_counter()
        status: CallStatus = "ok"
        try:
            return await provider.embed(model_name, texts, timeout=self._timeout)
        except ModelTimeoutError:
            status = "timeout"
            raise
        except ModelUnavailableError:
            status = "unavailable"
            raise
        except ModelError:
            status = "error"
            raise
        finally:
            await self._record(
                CallRecord(
                    context=context,
                    model=label,
                    status=status,
                    attempt=1,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                    prompt_tokens=sum(len(t.split()) for t in texts),
                    request=[{"role": "embed", "content": f"{len(texts)} text(s)"}],
                )
            )


def _strip_fences(text: str) -> str:
    """Some models wrap JSON in ``` fences even when asked not to."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def _short_error(error: ValidationError) -> str:
    first = error.errors()[0] if error.errors() else {"msg": str(error), "loc": ()}
    location = ".".join(str(part) for part in first.get("loc", ())) or "root"
    return f"{location}: {first.get('msg', 'invalid')}"[:300]


def build_gateway(settings: Settings, recorder: Recorder = _discard) -> ModelGateway:
    api_key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
    providers: dict[str, Provider] = {
        "ollama": OllamaProvider(settings.ollama_base_url),
        "fake": FakeProvider(latency_ms=settings.fake_latency_ms),
    }
    if settings.openai_base_url:
        providers["openai"] = OpenAICompatibleProvider(settings.openai_base_url, api_key)
    return ModelGateway(
        providers,
        recorder=recorder,
        timeout=settings.model_timeout_seconds,
        force_provider="fake" if settings.model_provider == "fake" else None,
    )
