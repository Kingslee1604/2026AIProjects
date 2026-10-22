"""Questionnaire run steps (R1-33), independent of the engine (architecture §7.4).

Each step opens its own tenant transaction, takes and returns IDs or small values only, and
is idempotent: an item that already has a result is never answered again (gate H1), and the
export writes one artifact per (run, epoch) whatever retries happen (gate H4). Steps record
`step_records` and `run_events`, the trace source for the UI.
"""

import asyncio
import hashlib
import json
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.agents.budget import BudgetExceededError
from agentforge.core.logging import get_logger
from agentforge.db.tenancy import TenantContext, tenant_session
from agentforge.knowledge.blobs import BlobStore
from agentforge.models import ModelError
from agentforge.qa.pipeline import INSUFFICIENT, GroundedAnswerer
from agentforge.qa.planner import BATCH_SIZE, ItemPlan, ItemToPlan, Planner
from agentforge.questionnaires.exporter import ExportRow, export_csv, export_xlsx
from agentforge.runs.events import emit

log = get_logger("agentforge.runs")

FINAL_ITEM_STATUSES = frozenset({"answered", "insufficient_evidence", "failed", "skipped"})
SKIPPED_KINDS = {
    "instruction": "Asks for a file or action, not an answer: handle manually.",
    "not_a_question": "A heading or note, not a question.",
}


class RunStoppedError(Exception):
    """The run is no longer running (cancelled, finished, or deleted)."""


@dataclass(frozen=True, slots=True)
class RunBudget:
    max_llm_calls: int
    max_seconds: float


@dataclass(frozen=True, slots=True)
class Usage:
    llm_calls: int
    tokens: int
    seconds: float


# Step records left "running" by a worker crash (the retry or the run's end closes them).
_INTERRUPTED = (
    "UPDATE app.step_records SET status = 'failed', error_class = 'WorkerInterrupted', "
    "error = 'the worker stopped during this attempt', finished_at = now() "
    "WHERE run_id = :r AND status = 'running'"
)


def export_key(run_id: UUID, epoch: int) -> str:
    return hashlib.sha256(f"{run_id}:export:{epoch}".encode()).hexdigest()


class QuestionnaireSteps:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        blobs: BlobStore,
        answerer: GroundedAnswerer,
        planner: Planner,
        *,
        default_budget: RunBudget,
    ) -> None:
        self._sessions = sessions
        self._blobs = blobs
        self._answerer = answerer
        self._planner = planner
        self._default_budget = default_budget

    def _tx(self, org_id: UUID):
        return tenant_session(self._sessions, TenantContext(org_id))

    # --- step records ----------------------------------------------------------------------

    @asynccontextmanager
    async def _step(
        self, org_id: UUID, run_id: UUID, node: str, item_index: int | None = None, epoch: int = 1
    ) -> AsyncGenerator[UUID]:
        """Record a step attempt: running, then succeeded or failed with the error class."""
        step_id = uuid4()
        async with self._tx(org_id) as db:
            # An earlier attempt still "running" was cut off by a worker crash.
            await db.execute(
                text(
                    f"{_INTERRUPTED} AND node_id = :n "
                    "AND item_index IS NOT DISTINCT FROM :i AND epoch = :e"
                ),
                {"r": run_id, "n": node, "i": item_index, "e": epoch},
            )
            await db.execute(
                text(
                    "INSERT INTO app.step_records (id, org_id, run_id, node_id, item_index, epoch, "
                    "attempt) SELECT :id, :o, :r, :n, :i, :e, 1 + count(*) FROM app.step_records "
                    "WHERE run_id = :r AND node_id = :n AND item_index IS NOT DISTINCT FROM :i "
                    "AND epoch = :e"
                ),
                {"id": step_id, "o": org_id, "r": run_id, "n": node, "i": item_index, "e": epoch},
            )
        try:
            yield step_id
        except BaseException as error:
            async with self._tx(org_id) as db:
                await db.execute(
                    text(
                        "UPDATE app.step_records SET status = :s, error_class = :c, error = :m, "
                        "finished_at = now() WHERE id = :id"
                    ),
                    {
                        "s": "cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
                        "c": error.__class__.__name__,
                        "m": str(error)[:500],
                        "id": step_id,
                    },
                )
            raise
        async with self._tx(org_id) as db:
            await db.execute(
                text(
                    "UPDATE app.step_records SET status = 'succeeded', finished_at = now() "
                    "WHERE id = :id"
                ),
                {"id": step_id},
            )

    async def _run(self, db: AsyncSession, run_id: UUID, *, lock: bool = False) -> Any:
        sql = "SELECT status, epoch, input, budgets, started_at FROM app.runs WHERE id = :r"
        row = (
            await db.execute(text(f"{sql} FOR UPDATE" if lock else sql), {"r": run_id})
        ).one_or_none()
        if row is None:
            raise RunStoppedError(f"run {run_id} not found")
        return row

    # --- steps ------------------------------------------------------------------------------

    async def begin(self, org_id: UUID, run_id: UUID) -> bool:
        """queued -> running. False when the run must not proceed (cancelled or finished)."""
        async with self._tx(org_id) as db:
            try:
                run = await self._run(db, run_id, lock=True)
            except RunStoppedError:
                return False
            if run.status == "queued":
                await db.execute(
                    text(
                        "UPDATE app.runs SET status = 'running', started_at = now(), "
                        "updated_at = now() WHERE id = :r"
                    ),
                    {"r": run_id},
                )
                await emit(db, org_id, run_id, "run_started")
                return True
            return run.status == "running"

    async def plan(self, org_id: UUID, run_id: UUID) -> list[int]:
        """Plan items that have no plan yet (in batches); return the indexes to answer."""
        async with self._tx(org_id) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT item_index, question, section, plan, status FROM app.run_items "
                        "WHERE run_id = :r ORDER BY item_index"
                    ),
                    {"r": run_id},
                )
            ).all()
        pending = [r for r in rows if not r.plan]
        for start in range(0, len(pending), BATCH_SIZE):
            batch = pending[start : start + BATCH_SIZE]
            try:
                async with self._step(org_id, run_id, "plan", batch[0].item_index) as step_id:
                    plans, version = await self._planner.plan_batch(
                        org_id,
                        [ItemToPlan(r.item_index, r.question, r.section) for r in batch],
                        run_id=run_id,
                        step_id=step_id,
                    )
            except ModelError as error:
                # Planning only improves answers: if the model keeps failing (the gateway has
                # already retried), answer these items as written rather than stall the run.
                log.warning("plan_fallback", run_id=str(run_id), error=error.__class__.__name__)
                plans, version = {r.item_index: ItemPlan() for r in batch}, "fallback"
            await self._store_plans(org_id, run_id, plans, version)
        async with self._tx(org_id) as db:
            planned = (
                await db.execute(
                    text(
                        "SELECT item_index, plan, status FROM app.run_items WHERE run_id = :r "
                        "ORDER BY item_index"
                    ),
                    {"r": run_id},
                )
            ).all()
            if pending:
                kinds: dict[str, int] = {}
                for row in planned:
                    kind = str(row.plan.get("kind", "question"))
                    kinds[kind] = kinds.get(kind, 0) + 1
                await emit(db, org_id, run_id, "plan_done", node_id="plan", data=kinds)
        return [r.item_index for r in planned if r.status not in FINAL_ITEM_STATUSES]

    async def _store_plans(
        self, org_id: UUID, run_id: UUID, plans: dict[int, ItemPlan], version: str
    ) -> None:
        async with self._tx(org_id) as db:
            for index, plan in plans.items():
                skipped = SKIPPED_KINDS.get(plan.kind)
                await db.execute(
                    text(
                        "UPDATE app.run_items SET plan = CAST(:p AS jsonb), "
                        "status = CASE WHEN :skip THEN 'skipped' ELSE status END, "
                        "reason = CASE WHEN :skip THEN :why ELSE reason END, "
                        "completed_at = CASE WHEN :skip THEN now() ELSE completed_at END "
                        "WHERE run_id = :r AND item_index = :i AND plan = '{}'::jsonb"
                    ),
                    {
                        "p": _json({**plan.to_json(), "planner_version": version}),
                        "skip": skipped is not None,
                        "why": skipped,
                        "r": run_id,
                        "i": index,
                    },
                )

    async def answer_item(self, org_id: UUID, run_id: UUID, index: int, epoch: int = 1) -> str:
        """Answer one item unless it already has a result. Returns the item status. Transient
        model errors propagate so the engine retries the step."""
        async with self._tx(org_id) as db:
            run = await self._run(db, run_id)
            if run.status not in {"running", "waiting_review"}:
                raise RunStoppedError(f"run is {run.status}")
            item = (
                await db.execute(
                    text(
                        "SELECT status, question, plan FROM app.run_items "
                        "WHERE run_id = :r AND item_index = :i FOR UPDATE"
                    ),
                    {"r": run_id, "i": index},
                )
            ).one_or_none()
            if item is None:
                raise RunStoppedError(f"item {index} not found")
            if item.status in FINAL_ITEM_STATUSES:
                return str(item.status)  # crash retry after commit: never answer twice
            await db.execute(
                text(
                    "UPDATE app.run_items SET status = 'answering' "
                    "WHERE run_id = :r AND item_index = :i"
                ),
                {"r": run_id, "i": index},
            )
            # With item_done / item_failed, this orders fan-out by event sequence, which does
            # not depend on the clock (gate H3 is checked this way; WSL2 clocks can step back).
            await emit(
                db, org_id, run_id, "item_started", node_id="answer", item_index=index, epoch=epoch
            )
        reason = await self.budget_problem(org_id, run_id)
        if reason is not None:
            await self.item_failed(org_id, run_id, index, "budget_exceeded", reason)
            return "failed"
        kb_ids = [UUID(k) for k in run.input.get("knowledge_base_ids", [])]
        plan: dict[str, Any] = dict(item.plan)
        async with self._step(org_id, run_id, "answer", index, epoch) as step_id:
            try:
                result = await self._answerer.answer(
                    org_id,
                    item.question,
                    kb_ids,
                    run_id=run_id,
                    step_id=step_id,
                    search_queries=plan.get("search_queries", []),
                    parts=plan.get("parts", []),
                )
            except BudgetExceededError as error:
                await self.item_failed(org_id, run_id, index, error.code, str(error))
                return "failed"
            async with self._tx(org_id) as db:
                await db.execute(
                    text(
                        "UPDATE app.run_items SET status = :s, answer = :a, confidence = :c, "
                        "reason = :why, claims = CAST(:cl AS jsonb), "
                        "evidence = CAST(:ev AS jsonb), "
                        "agent_versions = CAST(:av AS jsonb), timings = CAST(:t AS jsonb), "
                        "completed_at = now() WHERE run_id = :r AND item_index = :i"
                    ),
                    {
                        "s": result.status,
                        "a": result.answer,
                        "c": result.confidence,
                        "why": result.reason,
                        "cl": _json(result.claims_json()),
                        "ev": _json(result.evidence_json()),
                        "av": _json(result.agent_versions),
                        "t": _json({**result.timings, "budget": result.budget}),
                        "r": run_id,
                        "i": index,
                    },
                )
                # A retried item in an open review gets a fresh review (its old decision was
                # cleared when it was reopened) and a recomputed bulk-approval flag.
                await db.execute(
                    text(
                        "UPDATE app.review_items ri SET auto_accept_candidate = :a "
                        "FROM app.review_batches b WHERE b.id = ri.batch_id "
                        "AND b.run_id = :r AND b.status = 'open' AND ri.item_index = :i"
                    ),
                    {"a": _auto_accept(result.status, result.confidence), "r": run_id, "i": index},
                )
                await emit(
                    db,
                    org_id,
                    run_id,
                    "item_done",
                    node_id="answer",
                    item_index=index,
                    epoch=epoch,
                    step_id=step_id,
                    data={"status": result.status, "confidence": result.confidence},
                )
        return result.status

    async def item_failed(
        self, org_id: UUID, run_id: UUID, index: int, error_class: str, message: str
    ) -> None:
        async with self._tx(org_id) as db:
            await db.execute(
                text(
                    "UPDATE app.run_items SET status = 'failed', reason = :why, "
                    "completed_at = now() "
                    "WHERE run_id = :r AND item_index = :i AND status NOT IN "
                    "('answered', 'insufficient_evidence', 'skipped')"
                ),
                {"why": f"{error_class}: {message}"[:500], "r": run_id, "i": index},
            )
            await emit(
                db,
                org_id,
                run_id,
                "item_failed",
                node_id="answer",
                item_index=index,
                data={"error_class": error_class},
            )

    async def usage(self, org_id: UUID, run_id: UUID) -> Usage:
        async with self._tx(org_id) as db:
            calls = (
                await db.execute(
                    text(
                        "SELECT count(*) AS n, coalesce(sum(coalesce(prompt_tokens, 0) "
                        "+ coalesce(completion_tokens, 0)), 0) AS tokens "
                        "FROM app.llm_calls WHERE run_id = :r AND purpose <> 'retrieve'"
                    ),
                    {"r": run_id},
                )
            ).one()
            started = (
                await db.execute(
                    text(
                        "SELECT extract(epoch FROM now() - started_at) FROM app.runs WHERE id = :r"
                    ),
                    {"r": run_id},
                )
            ).scalar_one_or_none()
        return Usage(int(calls.n), int(calls.tokens), float(started or 0.0))

    async def budget_problem(self, org_id: UUID, run_id: UUID) -> str | None:
        """Why the run's budget is spent (model calls or wall-clock time), or None."""
        async with self._tx(org_id) as db:
            budgets = dict((await self._run(db, run_id)).budgets)
        limit = RunBudget(
            max_llm_calls=int(budgets.get("max_llm_calls", self._default_budget.max_llm_calls)),
            max_seconds=float(budgets.get("max_seconds", self._default_budget.max_seconds)),
        )
        used = await self.usage(org_id, run_id)
        if used.llm_calls >= limit.max_llm_calls:
            return f"model calls: {used.llm_calls} of {limit.max_llm_calls}"
        if used.seconds >= limit.max_seconds:
            return f"wall clock: {used.seconds:.0f} s of {limit.max_seconds:.0f} s"
        return None

    async def fail(self, org_id: UUID, run_id: UUID, error_class: str, error: str) -> None:
        async with self._tx(org_id) as db:
            moved = (
                await db.execute(
                    text(
                        "UPDATE app.runs SET status = 'failed', error_class = :c, error = :m, "
                        "finished_at = now(), updated_at = now() "
                        "WHERE id = :r AND status IN ('queued', 'running', 'waiting_review') "
                        "RETURNING id"
                    ),
                    {"c": error_class, "m": error[:500], "r": run_id},
                )
            ).one_or_none()
            if moved is not None:
                await db.execute(text(_INTERRUPTED), {"r": run_id})
                await emit(db, org_id, run_id, "run_failed", data={"error_class": error_class})

    async def route(self, org_id: UUID, run_id: UUID) -> dict[str, int]:
        """Open the review: one review item per answered item (skipped items need none), in the
        same transaction that moves the run to waiting_review. Verified high-confidence answers
        are candidates for bulk approval; the rest need a reviewer's attention (R1-34)."""
        async with self._tx(org_id) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT id, item_index, status, confidence FROM app.run_items "
                        "WHERE run_id = :r ORDER BY item_index"
                    ),
                    {"r": run_id},
                )
            ).all()
            counts = {"auto_accept_candidates": 0, "needs_review": 0, "skipped": 0}
            for row in rows:
                if row.status == "skipped":
                    counts["skipped"] += 1
                elif _auto_accept(row.status, row.confidence):
                    counts["auto_accept_candidates"] += 1
                else:
                    counts["needs_review"] += 1
            moved = (
                await db.execute(
                    text(
                        "UPDATE app.runs SET status = 'waiting_review', updated_at = now() "
                        "WHERE id = :r AND status = 'running' RETURNING epoch"
                    ),
                    {"r": run_id},
                )
            ).one_or_none()
            if moved is not None:
                batch_id = (
                    await db.execute(
                        text(
                            "INSERT INTO app.review_batches (org_id, run_id, epoch) "
                            "VALUES (:o, :r, :e) ON CONFLICT (run_id, epoch) DO UPDATE "
                            "SET status = app.review_batches.status RETURNING id"
                        ),
                        {"o": org_id, "r": run_id, "e": moved.epoch},
                    )
                ).scalar_one()
                reviewable = [r for r in rows if r.status != "skipped"]
                if reviewable:
                    await db.execute(
                        text(
                            "INSERT INTO app.review_items (org_id, batch_id, run_item_id, "
                            "item_index, auto_accept_candidate) VALUES (:o, :b, :ri, :i, :a) "
                            "ON CONFLICT (batch_id, item_index) DO NOTHING"
                        ),
                        [
                            {
                                "o": org_id,
                                "b": batch_id,
                                "ri": r.id,
                                "i": r.item_index,
                                "a": _auto_accept(r.status, r.confidence),
                            }
                            for r in reviewable
                        ],
                    )
                await emit(db, org_id, run_id, "review_waiting", node_id="review", data=counts)
        return counts

    async def leave_review(self, org_id: UUID, run_id: UUID) -> int:
        """waiting_review -> running; returns the epoch to export."""
        async with self._tx(org_id) as db:
            run = await self._run(db, run_id, lock=True)
            if run.status == "waiting_review":
                await db.execute(
                    text(
                        "UPDATE app.runs SET status = 'running', updated_at = now() WHERE id = :r"
                    ),
                    {"r": run_id},
                )
                await emit(db, org_id, run_id, "review_done", node_id="review")
            elif run.status != "running":
                raise RunStoppedError(f"run is {run.status}")
            return int(run.epoch)

    async def export(self, org_id: UUID, run_id: UUID, epoch: int) -> str:
        """Write the answers into a copy of the questionnaire: one artifact per (run, epoch)."""
        key = export_key(run_id, epoch)
        async with self._tx(org_id) as db:
            existing = (
                await db.execute(
                    text("SELECT id FROM app.artifacts WHERE idempotency_key = :k"), {"k": key}
                )
            ).scalar_one_or_none()
            if existing is not None:
                return str(existing)  # crash retry after commit: never export twice
            run = await self._run(db, run_id)
            questionnaire = (
                await db.execute(
                    text(
                        "SELECT filename, format, sheet, header_row, columns, blob_key "
                        "FROM app.questionnaires WHERE id = :q"
                    ),
                    {"q": run.input["questionnaire_id"]},
                )
            ).one()
            items = (
                await db.execute(
                    text(
                        # The latest review decision per item decides what is exported.
                        "SELECT i.source_ref, i.status, i.answer, i.claims, i.evidence, "
                        "rv.decision, rv.edited_answer FROM app.run_items i "
                        "LEFT JOIN LATERAL (SELECT ri.decision, ri.edited_answer "
                        "FROM app.review_items ri JOIN app.review_batches b ON b.id = ri.batch_id "
                        "WHERE ri.run_item_id = i.id ORDER BY b.created_at DESC LIMIT 1) rv "
                        "ON true WHERE i.run_id = :r ORDER BY i.item_index"
                    ),
                    {"r": run_id},
                )
            ).all()
        async with self._step(org_id, run_id, "export", None, epoch) as step_id:
            rows = [_export_row(item) for item in items if item.source_ref]
            suffix = ".xlsx" if questionnaire.format == "xlsx" else ".csv"
            stem = Path(questionnaire.filename).stem
            filename = f"{stem} - answered{'' if epoch == 1 else f' (v{epoch})'}{suffix}"
            blob_key = f"org/{org_id}/runs/{run_id}/export-{epoch}{suffix}"
            source = self._blobs.path(questionnaire.blob_key)
            answer_column = questionnaire.columns["indexes"]["answer"]
            with tempfile.TemporaryDirectory(prefix="export-") as scratch:
                target = Path(scratch) / f"export{suffix}"
                if questionnaire.format == "xlsx":
                    await asyncio.to_thread(
                        export_xlsx,
                        source,
                        target,
                        sheet=questionnaire.sheet,
                        header_row=questionnaire.header_row,
                        answer_column=answer_column,
                        rows=rows,
                    )
                else:
                    await asyncio.to_thread(
                        export_csv,
                        source,
                        target,
                        header_row=questionnaire.header_row,
                        answer_column=answer_column,
                        rows=rows,
                    )
                size = target.stat().st_size
                await asyncio.to_thread(self._blobs.put, blob_key, target)
            media_type = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if suffix == ".xlsx"
                else "text/csv"
            )
            async with self._tx(org_id) as db:
                artifact_id = (
                    await db.execute(
                        text(
                            "INSERT INTO app.artifacts (org_id, run_id, node_id, epoch, "
                            "idempotency_key, blob_key, filename, media_type, size_bytes) "
                            "VALUES (:o, :r, 'export', :e, :k, :b, :f, :m, :z) "
                            "ON CONFLICT (idempotency_key) DO UPDATE SET epoch = EXCLUDED.epoch "
                            "RETURNING id"
                        ),
                        {
                            "o": org_id,
                            "r": run_id,
                            "e": epoch,
                            "k": key,
                            "b": blob_key,
                            "f": filename,
                            "m": media_type,
                            "z": size,
                        },
                    )
                ).scalar_one()
                await emit(
                    db,
                    org_id,
                    run_id,
                    "export_done",
                    node_id="export",
                    epoch=epoch,
                    step_id=step_id,
                    data={"artifact_id": str(artifact_id)},
                )
        return str(artifact_id)

    async def finish(self, org_id: UUID, run_id: UUID, epoch: int) -> None:
        used = await self.usage(org_id, run_id)
        async with self._tx(org_id) as db:
            moved = (
                await db.execute(
                    text(
                        "UPDATE app.runs SET status = 'succeeded', finished_at = now(), "
                        "updated_at = now(), usage = CAST(:u AS jsonb) "
                        "WHERE id = :r AND status = 'running' RETURNING id"
                    ),
                    {
                        "u": _json(
                            {
                                "llm_calls": used.llm_calls,
                                "tokens": used.tokens,
                                "seconds": round(used.seconds, 1),
                            }
                        ),
                        "r": run_id,
                    },
                )
            ).one_or_none()
            if moved is not None:
                await db.execute(text(_INTERRUPTED), {"r": run_id})
                await emit(db, org_id, run_id, "run_succeeded", epoch=epoch)

    async def reopen_item(self, org_id: UUID, run_id: UUID, index: int) -> None:
        """User-initiated retry of one item: clear its result so it is answered again."""
        async with self._tx(org_id) as db:
            await db.execute(
                text(
                    "UPDATE app.run_items SET status = 'pending', answer = NULL, "
                    "confidence = NULL, reason = NULL, claims = '[]'::jsonb, "
                    "evidence = '[]'::jsonb, completed_at = NULL "
                    "WHERE run_id = :r AND item_index = :i AND status <> 'skipped'"
                ),
                {"r": run_id, "i": index},
            )
            # The new answer needs a new decision: clear this item's decision in the open review.
            await db.execute(
                text(
                    "UPDATE app.review_items ri SET decision = NULL, edited_answer = NULL, "
                    "reason = NULL, decided_by = NULL, decided_at = NULL, "
                    "auto_accept_candidate = false FROM app.review_batches b "
                    "WHERE b.id = ri.batch_id AND b.run_id = :r AND b.status = 'open' "
                    "AND ri.item_index = :i"
                ),
                {"r": run_id, "i": index},
            )


def _auto_accept(status: str, confidence: str | None) -> bool:
    """Verified, high-confidence answers may be approved in bulk; everything else is reviewed
    one by one."""
    return status == "answered" and confidence == "high"


def _sources(claims: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> str:
    by_id = {e["id"]: e for e in evidence}
    cited: list[str] = []
    for claim in claims:
        if not claim.get("kept"):
            continue
        for citation in claim.get("citations", []):
            e = by_id.get(citation)
            if e is None or e.get("deleted"):
                continue
            pages = (
                f"p. {e['page_start']}"
                if e["page_start"] == e["page_end"]
                else f"pp. {e['page_start']}-{e['page_end']}"
            )
            cited.append(f"{e['filename']} {pages}")
    return "; ".join(dict.fromkeys(cited))


def _export_row(item: Any) -> ExportRow:
    """What the customer gets: the reviewer's edit, nothing for a rejected answer (so a person
    fills it in), otherwise the verified answer."""
    sources = _sources(list(item.claims), list(item.evidence))
    if item.decision == "rejected":
        return ExportRow(row=int(item.source_ref), answer="", sources="")
    if item.decision == "edited":
        return ExportRow(row=int(item.source_ref), answer=item.edited_answer or "", sources=sources)
    if item.status == "answered":
        answer = item.answer or ""
    elif item.status == "insufficient_evidence":
        answer = INSUFFICIENT
    else:
        answer = ""
    return ExportRow(row=int(item.source_ref), answer=answer, sources=sources)


def _json(value: Any) -> str:
    return json.dumps(value, default=str)
