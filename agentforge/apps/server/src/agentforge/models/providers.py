"""Provider adapters: Ollama, OpenAI-compatible, and a deterministic fake for tests and demos."""

import asyncio
import hashlib
import json
import math
import random
import re
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import httpx2

from agentforge.models.types import (
    Message,
    ModelTimeoutError,
    ModelUnavailableError,
    ProviderError,
    ProviderResponse,
)

JsonSchema = dict[str, Any]


class Provider(Protocol):
    name: str

    async def chat(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        json_schema: JsonSchema | None,
        temperature: float,
        max_tokens: int | None,
        timeout: float,
    ) -> ProviderResponse: ...

    async def embed(
        self, model: str, texts: Sequence[str], *, timeout: float
    ) -> list[list[float]]: ...


def _raise_for_transport(error: Exception, provider: str) -> None:
    if isinstance(error, httpx2.TimeoutException):
        raise ModelTimeoutError(f"{provider}: timed out") from error
    if isinstance(error, httpx2.TransportError):
        raise ModelUnavailableError(
            f"{provider}: unreachable ({error.__class__.__name__})"
        ) from error


def _raise_for_status(response: httpx2.Response, provider: str) -> None:
    if response.status_code >= 500 or response.status_code == 404:
        raise ModelUnavailableError(
            f"{provider}: HTTP {response.status_code} {response.text[:200]}"
        )
    if response.status_code >= 400:
        raise ProviderError(f"{provider}: HTTP {response.status_code} {response.text[:200]}")


class OllamaProvider:
    """Ollama's native API: /api/chat with a JSON-schema `format`, /api/embed, /api/tags."""

    name = "ollama"

    def __init__(self, base_url: str, client: httpx2.AsyncClient | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._client = client or httpx2.AsyncClient()
        self._digests: dict[str, str] = {}

    async def _post(self, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        try:
            response = await self._client.post(f"{self._base}{path}", json=body, timeout=timeout)
        except Exception as error:
            _raise_for_transport(error, self.name)
            raise
        _raise_for_status(response, self.name)
        data: dict[str, Any] = response.json()
        return data

    async def _digest(self, model: str) -> str | None:
        if model not in self._digests:
            try:
                response = await self._client.get(f"{self._base}/api/tags", timeout=10)
                for entry in response.json().get("models", []):
                    self._digests[str(entry["name"])] = str(entry.get("digest", ""))[:12]
            except Exception:  # the digest is informational; never fail a call over it
                return None
        return self._digests.get(model)

    async def chat(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        json_schema: JsonSchema | None,
        temperature: float,
        max_tokens: int | None,
        timeout: float,
    ) -> ProviderResponse:
        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens:
            options["num_predict"] = max_tokens
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": options,
            # Reasoning models (e.g. Qwen3) would otherwise spend the budget on hidden thinking.
            "think": False,
        }
        if json_schema is not None:
            body["format"] = json_schema
        try:
            data = await self._post("/api/chat", body, timeout)
        except ProviderError as error:
            if "think" not in str(error).lower():
                raise
            del body["think"]  # models without a thinking mode reject the flag
            data = await self._post("/api/chat", body, timeout)
        return ProviderResponse(
            text=str(data.get("message", {}).get("content", "")),
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            model_digest=await self._digest(model),
        )

    async def embed(self, model: str, texts: Sequence[str], *, timeout: float) -> list[list[float]]:
        data = await self._post("/api/embed", {"model": model, "input": list(texts)}, timeout)
        return [[float(x) for x in vector] for vector in data["embeddings"]]


class OpenAICompatibleProvider:
    """Minimal OpenAI-compatible client (vLLM, llama.cpp server, LM servers)."""

    name = "openai"

    def __init__(
        self, base_url: str, api_key: str | None = None, client: httpx2.AsyncClient | None = None
    ) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx2.AsyncClient()

    async def _post(self, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"{self._base}{path}", json=body, headers=self._headers, timeout=timeout
            )
        except Exception as error:
            _raise_for_transport(error, self.name)
            raise
        _raise_for_status(response, self.name)
        data: dict[str, Any] = response.json()
        return data

    async def chat(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        json_schema: JsonSchema | None,
        temperature: float,
        max_tokens: int | None,
        timeout: float,
    ) -> ProviderResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": json_schema},
            }
        data = await self._post("/v1/chat/completions", body, timeout)
        usage: dict[str, Any] = data.get("usage") or {}
        return ProviderResponse(
            text=str(data["choices"][0]["message"]["content"] or ""),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    async def embed(self, model: str, texts: Sequence[str], *, timeout: float) -> list[list[float]]:
        data = await self._post("/v1/embeddings", {"model": model, "input": list(texts)}, timeout)
        return [[float(x) for x in item["embedding"]] for item in data["data"]]


# --- Fake provider --------------------------------------------------------------------------

FakeOutcome = str  # literal text, or one of the FAULTS below
FAULTS = ("timeout", "unavailable", "invalid_json")
Responder = Callable[[str, Sequence[Message], JsonSchema | None], str]
EMBED_DIM = 256
_WORD = re.compile(r"[a-z0-9]+")


def _seeded(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


def example_from_schema(schema: JsonSchema, seed: int, root: JsonSchema | None = None) -> Any:
    """A deterministic value that satisfies common JSON-schema shapes (Pydantic output)."""
    root = root or schema
    if "$ref" in schema:
        name = str(schema["$ref"]).rsplit("/", 1)[-1]
        return example_from_schema(root.get("$defs", {})[name], seed, root)
    if "anyOf" in schema:
        options = [s for s in schema["anyOf"] if s.get("type") != "null"] or schema["anyOf"]
        return example_from_schema(options[0], seed, root)
    if "enum" in schema:
        return schema["enum"][seed % len(schema["enum"])]
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if kind == "object":
        return {
            key: example_from_schema(sub, seed + i, root)
            for i, (key, sub) in enumerate(schema.get("properties", {}).items())
        }
    if kind == "array":
        count = max(int(schema.get("minItems", 1)), 1)
        return [example_from_schema(schema.get("items", {}), seed + i, root) for i in range(count)]
    if kind == "integer":
        return int(schema.get("minimum", 0)) + seed % 5
    if kind == "number":
        low = float(schema.get("minimum", 0.0))
        high = float(schema.get("maximum", low + 1.0))
        return low + (seed % 100) / 100 * (high - low)
    if kind == "boolean":
        return seed % 2 == 0
    return f"fake-{seed % 10_000}"


def _default_responder(model: str, messages: Sequence[Message], schema: JsonSchema | None) -> str:
    seed = _seeded(model + "\n".join(m.content for m in messages))
    if schema is None:
        return f"Fake answer {seed % 10_000} from {model}."
    return json.dumps(example_from_schema(schema, seed))


class FakeProvider:
    """Deterministic, offline provider for tests, CI, UI work, and engine benchmarks.

    `script` is consumed one entry per chat call: literal text, or a fault name
    ("timeout", "unavailable", "invalid_json"). When the script is empty, `responder` answers
    (default: schema-valid JSON derived from a hash of the prompt). With `fault_rate`, that
    share of unscripted calls gets a random fault instead (seeded; durability tests).
    Embeddings are hashed bag-of-words vectors, so texts that share words are close: good
    enough to test retrieval.
    """

    name = "fake"

    def __init__(
        self,
        *,
        script: Sequence[FakeOutcome] = (),
        responder: Responder | None = None,
        latency_ms: float = 0.0,
        fault_rate: float = 0.0,
        fault_seed: int = 0,
    ) -> None:
        self.script = list(script)
        self.responder = responder or _default_responder
        self.latency_ms = latency_ms
        self.fault_rate = fault_rate
        self._faults = random.Random(fault_seed)  # noqa: S311 - test fault injection, not crypto
        self.calls: list[tuple[str, list[Message]]] = []

    async def chat(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        json_schema: JsonSchema | None,
        temperature: float,
        max_tokens: int | None,
        timeout: float,
    ) -> ProviderResponse:
        self.calls.append((model, list(messages)))
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)
        if self.script:
            outcome = self.script.pop(0)
        elif self.fault_rate and self._faults.random() < self.fault_rate:
            outcome = self._faults.choice(FAULTS)
        else:
            outcome = self.responder(model, messages, json_schema)
        if outcome == "timeout":
            raise ModelTimeoutError("fake: injected timeout")
        if outcome == "unavailable":
            raise ModelUnavailableError("fake: injected outage")
        if outcome == "invalid_json":
            outcome = '{"this is": not valid json'
        words = sum(len(m.content.split()) for m in messages)
        return ProviderResponse(
            text=outcome,
            prompt_tokens=words,
            completion_tokens=len(outcome.split()),
            model_digest="fake0000",
        )

    async def embed(self, model: str, texts: Sequence[str], *, timeout: float) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * EMBED_DIM
            for word in _WORD.findall(text.lower()):
                vector[_seeded(word) % EMBED_DIM] += 1.0
            norm = math.sqrt(sum(x * x for x in vector)) or 1.0
            vectors.append([x / norm for x in vector])
        return vectors
