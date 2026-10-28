"""The usage record: one AI model call by one tenant.

The caller chooses `event_id`. A retry of the same call reuses it, which is what lets every
later stage drop duplicates.
"""

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class UsageEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    tenant_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    occurred_at: AwareDatetime

    @property
    def key(self) -> bytes:
        """Kafka key: one tenant's records share a partition, so their order is kept."""
        return self.tenant_id.encode()

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> "UsageEvent":
        return cls.model_validate_json(raw)
