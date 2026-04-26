import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, create_engine, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

from cold_email.config import settings


class Base(DeclarativeBase):
    pass


class Lead(Base):
    __tablename__ = "leads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_name = Column(String, nullable=False, index=True)
    founder_name = Column(String)
    founder_email = Column(String)
    linkedin_url = Column(String)
    company_url = Column(String)
    funding_stage = Column(String)
    headcount = Column(Integer)
    status = Column(String, nullable=False, default="found")
    error_msg = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    research = relationship("Research", back_populates="lead", cascade="all, delete-orphan")
    drafts = relationship("Draft", back_populates="lead", cascade="all, delete-orphan")


class Research(Base):
    __tablename__ = "research"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"))
    tech_stack = Column(JSONB)
    recent_news = Column(Text)
    hook = Column(Text)
    raw_content = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    lead = relationship("Lead", back_populates="research")


class Draft(Base):
    __tablename__ = "drafts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"))
    subject_line = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    version = Column(Integer, default=1)
    reviewer_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    lead = relationship("Lead", back_populates="drafts")


# Async engine — FastAPI uses this
async_engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)

# Sync engine — Celery workers use this
sync_engine = create_engine(settings.sync_database_url, echo=False)
SyncSessionLocal = sessionmaker(sync_engine, expire_on_commit=False)


async def get_async_session() -> AsyncSession:
    """FastAPI dependency — yields an async session per request."""
    async with AsyncSessionLocal() as session:
        yield session


def get_sync_session():
    """Celery helper — yields a sync session per task."""
    with SyncSessionLocal() as session:
        yield session
