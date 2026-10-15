"""DBOS workflows for questionnaire runs (R1-33), registered on import by the worker.

Shape (architecture §12.1): begin → plan → bounded fan-out (one child workflow per item on a
queue partitioned by run id, at most `item_parallelism` at a time) → join → budget check →
route → durable review wait (DBOS.recv: no thread or connection held) → export → finish.

Workflow and step arguments are IDs and small integers only (ADR-0002 condition, gate H6);
all content goes through the step library into RLS-protected app tables.
"""

from uuid import UUID

from dbos import DBOS, SetEnqueueOptions, SetWorkflowID, WorkflowHandleAsync

from agentforge.runs.dbos_engine import ITEM_QUEUE
from agentforge.runs.questionnaire import QuestionnaireSteps, RunStoppedError

REVIEW_TOPIC = "review"
REVIEW_WAIT_SECONDS = 24 * 3600  # recv re-arms after each timeout; the run waits indefinitely

_steps: QuestionnaireSteps | None = None


def configure(steps: QuestionnaireSteps) -> None:
    """Give the workflows their step library (the worker calls this before launching DBOS)."""
    global _steps
    _steps = steps


def _s() -> QuestionnaireSteps:
    if _steps is None:
        raise RuntimeError("workflows are not configured (worker only)")
    return _steps


def item_workflow_id(run: str, index: int, epoch: int) -> str:
    return f"run-{run}-item-{index}-e{epoch}"


# --- steps (thin wrappers: DBOS records each result) ------------------------------------------


@DBOS.step(name="questionnaire.begin")
async def s_begin(org: str, run: str) -> bool:
    return await _s().begin(UUID(org), UUID(run))


@DBOS.step(name="questionnaire.plan", retries_allowed=True, max_attempts=3, interval_seconds=5)
async def s_plan(org: str, run: str) -> list[int]:
    return await _s().plan(UUID(org), UUID(run))


@DBOS.step(name="questionnaire.answer", retries_allowed=True, max_attempts=3, interval_seconds=5)
async def s_answer(org: str, run: str, index: int, epoch: int) -> str:
    return await _s().answer_item(UUID(org), UUID(run), index, epoch)


@DBOS.step(name="questionnaire.item_failed")
async def s_item_failed(org: str, run: str, index: int, error_class: str) -> None:
    await _s().item_failed(UUID(org), UUID(run), index, error_class, "gave up after retries")


@DBOS.step(name="questionnaire.budget")
async def s_budget(org: str, run: str) -> str | None:
    return await _s().budget_problem(UUID(org), UUID(run))


@DBOS.step(name="questionnaire.fail")
async def s_fail(org: str, run: str, error_class: str, error: str) -> None:
    await _s().fail(UUID(org), UUID(run), error_class, error)


@DBOS.step(name="questionnaire.route")
async def s_route(org: str, run: str) -> dict[str, int]:
    return await _s().route(UUID(org), UUID(run))


@DBOS.step(name="questionnaire.leave_review")
async def s_leave_review(org: str, run: str) -> int:
    return await _s().leave_review(UUID(org), UUID(run))


@DBOS.step(name="questionnaire.export", retries_allowed=True, max_attempts=3, interval_seconds=2)
async def s_export(org: str, run: str, epoch: int) -> str:
    return await _s().export(UUID(org), UUID(run), epoch)


@DBOS.step(name="questionnaire.finish")
async def s_finish(org: str, run: str, epoch: int) -> None:
    await _s().finish(UUID(org), UUID(run), epoch)


@DBOS.step(name="questionnaire.reopen_item")
async def s_reopen_item(org: str, run: str, index: int) -> None:
    await _s().reopen_item(UUID(org), UUID(run), index)


# --- workflows --------------------------------------------------------------------------------


@DBOS.workflow(name="questionnaire.item")
async def item_workflow(org: str, run: str, index: int, epoch: int) -> str:
    try:
        return await s_answer(org, run, index, epoch)
    except RunStoppedError:
        return "stopped"
    except Exception as error:  # retries exhausted: this item fails, the run carries on
        await s_item_failed(org, run, index, error.__class__.__name__)
        return "failed"


@DBOS.workflow(name="questionnaire.run")
async def run_workflow(org: str, run: str) -> None:
    if not await s_begin(org, run):
        return
    indexes = await s_plan(org, run)
    queue = await DBOS.retrieve_queue_async(ITEM_QUEUE)
    assert queue is not None  # noqa: S101 - registered at launch
    handles: list[WorkflowHandleAsync[str]] = []
    for index in indexes:
        with (
            SetWorkflowID(item_workflow_id(run, index, 1)),
            SetEnqueueOptions(queue_partition_key=run),
        ):
            handles.append(await queue.enqueue_async(item_workflow, org, run, index, 1))
    statuses = [await handle.get_result() for handle in handles]
    if "stopped" in statuses:
        return  # cancelled or deleted while answering
    problem = await s_budget(org, run)
    if problem is not None:
        await s_fail(org, run, "budget_exceeded", problem)
        return
    await s_route(org, run)
    while await DBOS.recv_async(REVIEW_TOPIC, timeout_seconds=REVIEW_WAIT_SECONDS) is None:
        pass
    epoch = await s_leave_review(org, run)
    await s_export(org, run, epoch)
    await s_finish(org, run, epoch)


@DBOS.workflow(name="questionnaire.retry_item")
async def retry_item_workflow(org: str, run: str, index: int, epoch: int) -> str:
    """User-initiated retry of one item while the run waits for review: only that item is
    answered again (a new epoch, so its records are distinct from the first attempt)."""
    await s_reopen_item(org, run, index)
    return await item_workflow(org, run, index, epoch)


@DBOS.workflow(name="questionnaire.export")
async def export_workflow(org: str, run: str, epoch: int) -> None:
    """User-initiated re-export (new epoch, so a new artifact)."""
    await s_export(org, run, epoch)
    await s_finish(org, run, epoch)
