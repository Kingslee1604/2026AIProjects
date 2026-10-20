"""Builds the shared application services from settings (used by the API and the worker)."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from agentforge.agents.registry import AgentRegistry
from agentforge.core.settings import Settings
from agentforge.db.engine import create_app_engine, create_sessionmaker
from agentforge.knowledge.blobs import LocalBlobStore
from agentforge.knowledge.embeddings import EmbeddingTable, table_for
from agentforge.knowledge.retrieval import Retriever
from agentforge.knowledge.service import KnowledgeService
from agentforge.models import ModelGateway, build_gateway
from agentforge.models.providers import FakeProvider
from agentforge.models.recorder import DbRecorder
from agentforge.overview import OverviewService
from agentforge.qa.fake import with_grounded_answers
from agentforge.qa.pipeline import GroundedAnswerer, QAConfig
from agentforge.qa.planner import Planner
from agentforge.qa.purge import purge_documents
from agentforge.qa.service import QAService
from agentforge.questionnaires.service import QuestionnaireService
from agentforge.reviews.service import ReviewService
from agentforge.runs.dbos_engine import DbosEngine
from agentforge.runs.port import NoEngine, RunEngine
from agentforge.runs.questionnaire import QuestionnaireSteps, RunBudget
from agentforge.runs.service import Limits, RunService


@dataclass(frozen=True, slots=True)
class Services:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    gateway: ModelGateway
    embedding_table: EmbeddingTable
    knowledge: KnowledgeService
    retriever: Retriever
    agents: AgentRegistry
    qa: QAService
    answerer: GroundedAnswerer
    run_engine: RunEngine
    runs: RunService
    blobs: LocalBlobStore
    questionnaires: QuestionnaireService
    questionnaire_steps: QuestionnaireSteps
    reviews: ReviewService
    overview: OverviewService

    async def close(self) -> None:
        if isinstance(self.run_engine, DbosEngine):
            self.run_engine.close()
        await self.engine.dispose()


FAKE_MAX_DISTANCE = 0.85  # fake bag-of-words embeddings sit further apart than real ones


def qa_config(settings: Settings) -> QAConfig:
    max_distance = settings.qa_max_distance
    if settings.model_provider == "fake" and "qa_max_distance" not in settings.model_fields_set:
        max_distance = FAKE_MAX_DISTANCE
    return QAConfig(
        chat_model=settings.chat_model,
        fallback_models=settings.chat_fallback_models,
        top_k=settings.qa_top_k,
        max_distance=max_distance,
        verifier_mode=settings.qa_verifier_mode,
        relevance_check=settings.qa_relevance_check,
        max_llm_calls=settings.qa_max_llm_calls,
    )


def build_services(settings: Settings, *, pool_size: int = 5) -> Services:
    if settings.database_url is None:
        raise ValueError("AGENTFORGE_DATABASE_URL is required")
    engine = create_app_engine(settings.database_url, pool_size=pool_size)
    sessions = create_sessionmaker(engine)
    gateway = build_gateway(settings, DbRecorder(sessions))
    table = table_for(settings.embedding_model, fake=settings.model_provider == "fake")
    retriever = Retriever(
        sessions, gateway, embedding_model=settings.embedding_model, embedding_table=table
    )
    agents = AgentRegistry(sessions)
    fake = gateway.provider("fake")
    if isinstance(fake, FakeProvider):  # fake mode answers questions from the evidence
        fake.responder = with_grounded_answers(fake.responder)
    run_engine: RunEngine = (
        DbosEngine(settings) if settings.engine_database_url is not None else NoEngine()
    )
    blobs = LocalBlobStore(settings.blob_root)
    answerer = GroundedAnswerer(gateway, retriever, agents, qa_config(settings))
    runs = RunService(
        sessions,
        run_engine,
        Limits(max_llm_calls=settings.run_max_llm_calls, max_seconds=settings.run_max_seconds),
    )
    return Services(
        engine=engine,
        sessions=sessions,
        gateway=gateway,
        embedding_table=table,
        run_engine=run_engine,
        runs=runs,
        reviews=ReviewService(sessions, runs),
        overview=OverviewService(
            sessions,
            gateway,
            model_provider=settings.model_provider,
            chat_model=settings.chat_model,
            embedding_model=settings.embedding_model,
        ),
        blobs=blobs,
        questionnaires=QuestionnaireService(
            sessions, blobs, max_bytes=settings.max_upload_mb * 1024 * 1024
        ),
        knowledge=KnowledgeService(
            sessions,
            blobs,
            gateway,
            embedding_model=settings.embedding_model,
            embedding_table=table,
            purge_hooks=[purge_documents],
        ),
        retriever=retriever,
        agents=agents,
        qa=QAService(sessions, answerer),
        answerer=answerer,
        questionnaire_steps=QuestionnaireSteps(
            sessions,
            blobs,
            answerer,
            Planner(gateway, agents, settings.chat_model),
            default_budget=RunBudget(
                max_llm_calls=settings.run_max_llm_calls, max_seconds=settings.run_max_seconds
            ),
        ),
    )
