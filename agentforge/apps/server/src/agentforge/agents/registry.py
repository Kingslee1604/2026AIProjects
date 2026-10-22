"""Loads agent definitions (TOML) and records each distinct definition as an immutable version.

Release 1 defines agents as files in the repository (ADR-11, code-first). The first time an
organization uses an agent, and whenever its file changes, a new row is appended to
app.agent_versions keyed by the definition's checksum; existing versions never change.
"""

import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.db.tenancy import TenantContext, tenant_session

DEFINITIONS_DIR = Path(__file__).resolve().parent / "definitions"


class Limits(BaseModel):
    max_llm_calls: int = Field(default=4, ge=1, le=50)


class AgentDefinition(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    name: str
    description: str = ""
    model: str = "default"  # "default" means settings.chat_model
    fallbacks: list[str] = Field(default_factory=lambda: [])
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=16, le=8192)
    limits: Limits = Field(default_factory=Limits)
    system_prompt: str

    @property
    def checksum(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class AgentVersion:
    id: UUID
    key: str
    version: int
    definition: AgentDefinition


def load_definitions(directory: Path = DEFINITIONS_DIR) -> dict[str, AgentDefinition]:
    definitions: dict[str, AgentDefinition] = {}
    for path in sorted(directory.glob("*.toml")):
        definition = AgentDefinition.model_validate(tomllib.loads(path.read_text("utf-8")))
        definition.system_prompt = definition.system_prompt.strip()
        definitions[definition.key] = definition
    return definitions


class AgentRegistry:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        definitions: dict[str, AgentDefinition] | None = None,
    ) -> None:
        self._sessions = sessions
        self._definitions = definitions if definitions is not None else load_definitions()
        self._cache: dict[tuple[UUID, str], AgentVersion] = {}

    def definition(self, key: str) -> AgentDefinition:
        return self._definitions[key]

    async def version(self, org_id: UUID, key: str) -> AgentVersion:
        """The org's current version of an agent, creating it if the definition is new."""
        cached = self._cache.get((org_id, key))
        if cached is not None:
            return cached
        definition = self._definitions[key]
        checksum = definition.checksum
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            agent_id = (
                await db.execute(
                    text(
                        "INSERT INTO app.agents (org_id, key, name) VALUES (:o, :k, :n) "
                        "ON CONFLICT (org_id, key) DO UPDATE SET name = EXCLUDED.name "
                        "RETURNING id"
                    ),
                    {"o": org_id, "k": key, "n": definition.name},
                )
            ).scalar_one()
            await db.execute(
                text(
                    "INSERT INTO app.agent_versions "
                    "(org_id, agent_id, version, checksum, definition) "
                    "SELECT :o, :a, coalesce(max(version), 0) + 1, :c, CAST(:d AS jsonb) "
                    "FROM app.agent_versions WHERE agent_id = :a "
                    "ON CONFLICT (agent_id, checksum) DO NOTHING"
                ),
                {"o": org_id, "a": agent_id, "c": checksum, "d": definition.model_dump_json()},
            )
            row = (
                await db.execute(
                    text(
                        "SELECT id, version FROM app.agent_versions "
                        "WHERE agent_id = :a AND checksum = :c"
                    ),
                    {"a": agent_id, "c": checksum},
                )
            ).one()
        version = AgentVersion(row.id, key, row.version, definition)
        self._cache[(org_id, key)] = version
        return version
