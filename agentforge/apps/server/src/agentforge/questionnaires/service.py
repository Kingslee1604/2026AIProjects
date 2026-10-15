"""Store uploaded questionnaires: the original file as a blob (the export writes into a copy of
it) and the parsed items, which become run items when a run starts."""

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.core.logging import get_logger
from agentforge.db.tenancy import TenantContext, tenant_session
from agentforge.knowledge.blobs import BlobStore
from agentforge.questionnaires.importer import (
    ColumnMapping,
    ParsedItem,
    ParsedQuestionnaire,
    parse_questionnaire,
)

log = get_logger("agentforge.questionnaires")


class NotFoundError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Questionnaire:
    id: UUID
    filename: str
    media_type: str
    size_bytes: int
    format: str
    sheet: str | None
    header_row: int
    columns: dict[str, Any]
    headers: list[str]
    items: list[dict[str, Any]]
    item_count: int
    warnings: list[str]
    created_at: datetime

    def parsed_items(self) -> list[ParsedItem]:
        return [ParsedItem(**item) for item in self.items]


def _columns_json(mapping: ColumnMapping) -> dict[str, Any]:
    return {"indexes": asdict(mapping), "letters": mapping.letters()}


def mapping_from_json(columns: dict[str, Any]) -> ColumnMapping:
    return ColumnMapping(**columns["indexes"])


_COLUMNS = (
    "id, filename, media_type, size_bytes, format, sheet, header_row, columns, headers, items, "
    "item_count, warnings, created_at"
)


def _record(row: Any) -> Questionnaire:
    return Questionnaire(
        id=row.id,
        filename=row.filename,
        media_type=row.media_type,
        size_bytes=row.size_bytes,
        format=row.format,
        sheet=row.sheet,
        header_row=row.header_row,
        columns=dict(row.columns),
        headers=list(row.headers),
        items=list(row.items),
        item_count=row.item_count,
        warnings=list(row.warnings),
        created_at=row.created_at,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class QuestionnaireService:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], blobs: BlobStore, *, max_bytes: int
    ) -> None:
        self._sessions = sessions
        self._blobs = blobs
        self._max_bytes = max_bytes

    async def upload(
        self, org_id: UUID, user_id: UUID | None, upload: Path, filename: str, size: int
    ) -> Questionnaire:
        """Validate and parse (raises UploadRejectedError with a user-facing message), then
        store the original file and the parsed items."""
        parsed = await asyncio.to_thread(
            parse_questionnaire, upload, filename, size, max_bytes=self._max_bytes
        )
        digest = await asyncio.to_thread(_sha256, upload)
        questionnaire_id = uuid4()
        blob_key = f"org/{org_id}/questionnaires/{questionnaire_id}"
        await asyncio.to_thread(self._blobs.put, blob_key, upload)
        try:
            async with tenant_session(self._sessions, TenantContext(org_id)) as db:
                row = (
                    await db.execute(
                        text(
                            "INSERT INTO app.questionnaires (id, org_id, created_by, filename, "  # noqa: S608
                            "media_type, size_bytes, sha256, blob_key, format, sheet, "
                            "header_row, columns, headers, items, item_count, warnings) VALUES "
                            "(:id, :o, :u, :f, :m, :z, :s, :b, :fmt, :sh, :hr, "
                            "CAST(:c AS jsonb), CAST(:h AS jsonb), CAST(:i AS jsonb), :n, "
                            f"CAST(:w AS jsonb)) RETURNING {_COLUMNS}"
                        ),
                        {
                            "id": questionnaire_id,
                            "o": org_id,
                            "u": user_id,
                            "z": size,
                            "s": digest,
                            "b": blob_key,
                            **self._parsed_params(parsed),
                        },
                    )
                ).one()
        except Exception:
            self._blobs.delete(blob_key)
            raise
        log.info(
            "questionnaire_uploaded",
            questionnaire_id=str(questionnaire_id),
            items=len(parsed.items),
        )
        return _record(row)

    @staticmethod
    def _parsed_params(parsed: ParsedQuestionnaire) -> dict[str, Any]:
        return {
            "f": parsed.filename,
            "m": parsed.media_type,
            "fmt": parsed.format,
            "sh": parsed.sheet,
            "hr": parsed.header_row,
            "c": json.dumps(_columns_json(parsed.columns)),
            "h": json.dumps(parsed.headers),
            "i": json.dumps([asdict(item) for item in parsed.items]),
            "n": len(parsed.items),
            "w": json.dumps(parsed.warnings),
        }

    async def get(self, org_id: UUID, questionnaire_id: UUID) -> Questionnaire:
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            row = (
                await db.execute(
                    text(f"SELECT {_COLUMNS} FROM app.questionnaires WHERE id = :q"),  # noqa: S608
                    {"q": questionnaire_id},
                )
            ).one_or_none()
        if row is None:
            raise NotFoundError("questionnaire not found")
        return _record(row)

    async def blob_key(self, org_id: UUID, questionnaire_id: UUID) -> str:
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            key = (
                await db.execute(
                    text("SELECT blob_key FROM app.questionnaires WHERE id = :q"),
                    {"q": questionnaire_id},
                )
            ).scalar_one_or_none()
        if key is None:
            raise NotFoundError("questionnaire not found")
        return str(key)

    async def remap(
        self, org_id: UUID, questionnaire_id: UUID, mapping: ColumnMapping
    ) -> Questionnaire:
        """Re-read the stored file with columns chosen by the user."""
        current = await self.get(org_id, questionnaire_id)
        path = self._blobs.path(await self.blob_key(org_id, questionnaire_id))
        parsed = await asyncio.to_thread(
            parse_questionnaire,
            path,
            current.filename,
            current.size_bytes,
            max_bytes=self._max_bytes,
            mapping=mapping,
        )
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            row = (
                await db.execute(
                    text(
                        "UPDATE app.questionnaires SET filename = :f, media_type = :m, "  # noqa: S608
                        "format = :fmt, sheet = :sh, header_row = :hr, "
                        "columns = CAST(:c AS jsonb), headers = CAST(:h AS jsonb), "
                        "items = CAST(:i AS jsonb), item_count = :n, warnings = CAST(:w AS jsonb) "
                        f"WHERE id = :id RETURNING {_COLUMNS}"
                    ),
                    {"id": questionnaire_id, **self._parsed_params(parsed)},
                )
            ).one()
        return _record(row)
