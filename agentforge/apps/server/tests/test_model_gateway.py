"""Model gateway behavior with the fake provider and a mocked Ollama (no network, no model)."""

import json
import uuid
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel, Field

from agentforge.models import (
    CallContext,
    Message,
    ModelError,
    ModelGateway,
    ModelTimeoutError,
    StructuredOutputError,
)
from agentforge.models.providers import FakeProvider, OllamaProvider, example_from_schema
from agentforge.models.types import CallRecord, ModelRef

pytestmark = pytest.mark.anyio
CTX = CallContext(org_id=uuid.uuid4(), purpose="test")
ASK = [Message("user", "What is the capital of France?")]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Answer(BaseModel):
    answer: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


def gateway(*providers: FakeProvider) -> tuple[ModelGateway, list[CallRecord]]:
    records: list[CallRecord] = []

    async def recorder(record: CallRecord) -> None:
        records.append(record)

    named = {f"m{i}" if i else "fake": p for i, p in enumerate(providers)}
    return ModelGateway(named, recorder=recorder), records


VALID = json.dumps({"answer": "Paris", "confidence": 0.9})


async def test_fault_injection_is_seeded_and_off_by_default() -> None:
    async def outcomes(provider: FakeProvider) -> list[str]:
        seen: list[str] = []
        for _ in range(200):
            try:
                response = await provider.chat(
                    "m", ASK, json_schema=None, temperature=0, max_tokens=None, timeout=1
                )
            except ModelError as error:
                seen.append(error.__class__.__name__)
            else:
                seen.append("invalid_json" if response.text.startswith('{"this') else "ok")
        return seen

    first = await outcomes(FakeProvider(fault_rate=0.3, fault_seed=7))
    assert first == await outcomes(FakeProvider(fault_rate=0.3, fault_seed=7))  # replayable
    assert {"ModelTimeoutError", "ModelUnavailableError", "invalid_json", "ok"} == set(first)
    assert 0.2 < sum(o != "ok" for o in first) / len(first) < 0.4
    assert set(await outcomes(FakeProvider())) == {"ok"}


def test_model_ref_parsing_keeps_colons_in_the_model_name() -> None:
    assert ModelRef.parse("ollama:qwen3:4b") == ModelRef("ollama", "qwen3:4b")
    with pytest.raises(ValueError, match="provider:model"):
        ModelRef.parse("qwen3")


async def test_valid_structured_output_on_first_try() -> None:
    gw, records = gateway(FakeProvider(script=[VALID]))
    parsed, result = await gw.generate_structured(CTX, ASK, Answer, model="fake:a")
    assert parsed == Answer(answer="Paris", confidence=0.9)
    assert result.attempts == 1
    assert [r.status for r in records] == ["ok"]  # exactly one record per provider call


async def test_invalid_output_is_repaired_with_the_validation_error() -> None:
    provider = FakeProvider(script=['{"answer": "", "confidence": 3}', VALID])
    gw, records = gateway(provider)
    parsed, result = await gw.generate_structured(CTX, ASK, Answer, model="fake:a")
    assert parsed.answer == "Paris"
    assert result.attempts == 2
    assert [r.status for r in records] == ["invalid_output", "ok"]
    repair_prompt = provider.calls[1][1][-1].content
    assert "not valid for the required JSON schema" in repair_prompt
    assert "answer" in repair_prompt  # the specific validation error is fed back


async def test_falls_back_to_the_next_model_after_a_failed_repair() -> None:
    primary = FakeProvider(script=["invalid_json", "still not json"])
    backup = FakeProvider(script=[VALID])
    gw, records = gateway(primary, backup)
    parsed, result = await gw.generate_structured(
        CTX, ASK, Answer, model="fake:a", fallbacks=["m1:b"]
    )
    assert parsed.answer == "Paris"
    assert result.model == "m1:b"
    assert [r.status for r in records] == ["invalid_output", "invalid_output", "ok"]


async def test_timeout_skips_repair_and_falls_back() -> None:
    gw, records = gateway(FakeProvider(script=["timeout"]), FakeProvider(script=[VALID]))
    _, result = await gw.generate_structured(CTX, ASK, Answer, model="fake:a", fallbacks=["m1:b"])
    assert result.model == "m1:b"
    assert [r.status for r in records] == ["timeout", "ok"]


async def test_all_models_failing_raises_a_retryable_error_listing_each_failure() -> None:
    gw, _ = gateway(FakeProvider(script=["timeout"]), FakeProvider(script=["x", "y"]))
    with pytest.raises(StructuredOutputError) as caught:
        await gw.generate_structured(CTX, ASK, Answer, model="fake:a", fallbacks=["m1:b"])
    assert caught.value.retryable
    assert "fake:a: ModelTimeoutError" in str(caught.value)
    assert "m1:b: invalid output" in str(caught.value)


async def test_free_text_generation_classifies_timeouts() -> None:
    gw, records = gateway(FakeProvider(script=["timeout"]))
    with pytest.raises(ModelTimeoutError) as caught:
        await gw.generate(CTX, ASK, model="fake:a")
    assert caught.value.retryable
    assert records[0].status == "timeout"


async def test_fenced_json_is_accepted() -> None:
    gw, _ = gateway(FakeProvider(script=[f"```json\n{VALID}\n```"]))
    parsed, _ = await gw.generate_structured(CTX, ASK, Answer, model="fake:a")
    assert parsed.answer == "Paris"


async def test_default_fake_output_is_deterministic_and_schema_valid() -> None:
    gw, _ = gateway(FakeProvider())
    first, _ = await gw.generate_structured(CTX, ASK, Answer, model="fake:a")
    second, _ = await gw.generate_structured(CTX, ASK, Answer, model="fake:a")
    assert first == second
    schema = Answer.model_json_schema()
    assert Answer.model_validate(example_from_schema(schema, 7))


async def test_fake_embeddings_are_normalized_and_reflect_shared_words() -> None:
    gw, records = gateway(FakeProvider())
    a, b, c = await gw.embed(
        CTX,
        [
            "access logs are retained for ninety days",
            "how long are access logs retained",
            "the cafeteria menu",
        ],
        model="fake:embed",
    )
    dot = lambda x, y: sum(i * j for i, j in zip(x, y, strict=True))  # noqa: E731
    assert abs(dot(a, a) - 1) < 1e-9
    assert dot(a, b) > dot(a, c)
    assert records[0].context.purpose == "test"
    assert records[0].status == "ok"


async def test_force_fake_routes_every_reference_to_the_fake_provider() -> None:
    records: list[CallRecord] = []

    async def recorder(record: CallRecord) -> None:
        records.append(record)

    gw = ModelGateway(
        {"fake": FakeProvider(script=[VALID])}, recorder=recorder, force_provider="fake"
    )
    parsed, result = await gw.generate_structured(CTX, ASK, Answer, model="ollama:qwen3:4b")
    assert parsed.answer == "Paris"
    assert result.model == "fake:qwen3:4b"


# --- Ollama adapter against a mocked HTTP server --------------------------------------------


def mocked_ollama(handler: Any) -> OllamaProvider:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return OllamaProvider("http://ollama.test", client=client)


async def test_ollama_chat_sends_schema_and_reads_token_counts() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/tags":
            return httpx2.Response(
                200, json={"models": [{"name": "qwen3:4b", "digest": "abc123def4567890"}]}
            )
        seen.update(json.loads(request.content))
        return httpx2.Response(
            200, json={"message": {"content": VALID}, "prompt_eval_count": 12, "eval_count": 7}
        )

    gw = ModelGateway({"ollama": mocked_ollama(handler)})
    parsed, result = await gw.generate_structured(CTX, ASK, Answer, model="ollama:qwen3:4b")
    assert parsed.answer == "Paris"
    assert seen["format"]["properties"]["answer"]["type"] == "string"
    assert seen["stream"] is False
    assert seen["think"] is False
    assert (result.prompt_tokens, result.completion_tokens) == (12, 7)
    assert result.model_digest == "abc123def456"


def _missing_model(_: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(404, json={"error": "model 'nope' not found"})


def _slow(_: httpx2.Request) -> httpx2.Response:
    raise httpx2.ReadTimeout("slow")


@pytest.mark.parametrize(
    ("handler", "expected"), [(_missing_model, "unavailable"), (_slow, "timeout")]
)
async def test_ollama_errors_map_to_retryable_classes(handler: Any, expected: str) -> None:
    records: list[CallRecord] = []

    async def recorder(record: CallRecord) -> None:
        records.append(record)

    gw = ModelGateway({"ollama": mocked_ollama(handler)}, recorder=recorder)
    with pytest.raises(StructuredOutputError):
        await gw.generate_structured(CTX, ASK, Answer, model="ollama:nope")
    assert records[0].status == expected


async def test_ollama_retries_without_think_flag_when_model_rejects_it() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/tags":
            return httpx2.Response(200, json={"models": []})
        body = json.loads(request.content)
        bodies.append(body)
        if "think" in body:
            return httpx2.Response(400, json={"error": "model does not support thinking"})
        return httpx2.Response(200, json={"message": {"content": VALID}})

    gw = ModelGateway({"ollama": mocked_ollama(handler)})
    parsed, _ = await gw.generate_structured(CTX, ASK, Answer, model="ollama:phi4-mini")
    assert parsed.answer == "Paris"
    assert ["think" in b for b in bodies] == [True, False]
