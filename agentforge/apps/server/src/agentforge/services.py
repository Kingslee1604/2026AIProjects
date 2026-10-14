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
from agentforge.qa.fake import with_grounded_answers
from agentforge.qa.pipeline import GroundedAnswerer, QAConfig
from agentforge.qa.purge import purge_documents
from agentforge.qa.service import QAService


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

    async def close(self) -> None:
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
    return Services(
        engine=engine,
        sessions=sessions,
        gateway=gateway,
        embedding_table=table,
        knowledge=KnowledgeService(
            sessions,
            LocalBlobStore(settings.blob_root),
            gateway,
            embedding_model=settings.embedding_model,
            embedding_table=table,
            purge_hooks=[purge_documents],
        ),
        retriever=retriever,
        agents=agents,
        qa=QAService(sessions, GroundedAnswerer(gateway, retriever, agents, qa_config(settings))),
    )
