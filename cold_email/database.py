import contextlib
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

from cold_email.config import settings


class Base(DeclarativeBase):
    pass


ROLE_USER = "user"
ROLE_ADMIN = "admin"


class User(Base):
    """An authenticated person.

    `role` is TEXT rather than a Postgres enum: extending an enum requires a
    migration, and a future 'viewer' or 'owner' role should not need DDL.
    Validity is enforced in the application layer.
    """

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    google_sub = Column(String, unique=True, index=True)
    email = Column(String, unique=True, nullable=False)
    name = Column(String)
    picture_url = Column(String)
    role = Column(String, nullable=False, default=ROLE_USER)
    # Fernet ciphertext of the Gmail refresh token — never a plaintext token.
    gmail_refresh_token_enc = Column(LargeBinary)
    gmail_sender_email = Column(String)
    # Optional BYOK: bypasses both the platform quota and the shared token
    # bucket, since the user is spending their own limits.
    llm_api_key_enc = Column(LargeBinary)
    llm_provider = Column(String)  # groq | gemini
    # server_default (not just the ORM-side `default`) so create_all and
    # migration 008 (`NOT NULL DEFAULT 100`) provision byte-identical DDL —
    # otherwise a raw SQL INSERT that never goes through the ORM's `default`
    # would violate NOT NULL on a create_all-provisioned table.
    monthly_draft_quota = Column(Integer, nullable=False, default=100, server_default=text("100"))
    # NULL = send immediately on approve. See cold_email/cadence.py.
    send_cadence = Column(JSONB)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


class Profile(Base):
    """Per-user sender identity: the fields the email template fills.

    Replaces sender_profile.PROFILE. The SenderProfile dataclass still exists as
    the in-memory shape — only its source changed, from a module constant to
    this row (see SenderProfile.from_row).

    resume_pdf reads and writes go through cold_email.resume_store, never
    directly, so a future move to GCS is one implementation swap.
    """

    __tablename__ = "profiles"

    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    name = Column(String, nullable=False)
    intro = Column(Text, nullable=False)
    linkedin = Column(String)
    github = Column(String)
    website = Column(String)
    experience_pool = Column(JSONB, nullable=False, default=list)
    company_links = Column(JSONB, nullable=False, default=dict)
    resume_pdf = Column(LargeBinary)
    resume_filename = Column(String)
    resume_text = Column(Text)
    parsed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def has_resume(self) -> bool:
        return self.resume_pdf is not None


# Global research lifecycle — a fact about a company, true for every user.
RESEARCH_FOUND = "found"
RESEARCH_RESEARCHED = "researched"
RESEARCH_FAILED = "failed"

# Per-user outreach lifecycle. 'sending' is added in Stack 4.
OUTREACH_QUEUED = "queued"
# A transient CLAIM, not a user-facing lifecycle stage: drafting_task moves a
# row here atomically (UPDATE ... WHERE status = 'queued' ... RETURNING) right
# before working it, so a second concurrent dispatch reading the same
# still-queued rows sees them already gone and skips them instead of drafting
# them a second time. A transient failure on a claimed row reverts it to
# 'queued' rather than leaving it stuck here. A row that never gets that
# chance — a hard process kill (OOM, SIGKILL, container eviction) between the
# claim and the row finishing, not a Python exception — is instead reclaimed
# by drafting_recovery_task once Outreach.updated_at shows the claim is stale
# (see Outreach.reclaim_count).
OUTREACH_DRAFTING = "drafting"
OUTREACH_DRAFTED = "drafted"
OUTREACH_APPROVED = "approved"
# A transient CLAIM, not a user-facing lifecycle stage — the send-side twin of
# OUTREACH_DRAFTING. send_due_task moves a row here atomically (UPDATE ...
# WHERE status = 'approved' ... RETURNING) right before dispatching
# logistics_task, so two overlapping scans (or Celery redelivering the same
# dispatch) cannot both send it: Celery guarantees at-least-once TASK
# delivery, so a scanner over rows that only leave the set on success would
# eventually dispatch one twice, and a cold email sent twice to a founder
# cannot be undone.
#
# Unlike a stale 'drafting' claim, a stale 'sending' claim is NEVER
# auto-reclaimed back to 'approved': no Gmail draft exists yet when a row is
# claimed for drafting, but a 'sending' claim means send_draft may already
# have been called, so retrying is exactly how a duplicate send happens. A
# row stuck here past STUCK_SENDING_MINUTES is dead-lettered by
# reap_stuck_sends for a human to verify the mailbox before retrying — never
# requeued automatically.
OUTREACH_SENDING = "sending"
OUTREACH_SENT = "sent"
OUTREACH_REJECTED = "rejected"
OUTREACH_FAILED = "failed"


class Company(Base):
    """A company in the global pool: discovered and researched once, reused by
    every user. Holds only facts true for everyone — no per-user state.
    """

    __tablename__ = "companies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_name = Column(String, nullable=False)
    company_url = Column(String)
    linkedin_url = Column(String)
    founder_name = Column(String)
    funding_stage = Column(String)
    headcount = Column(Integer)
    industry = Column(String)
    research_status = Column(String, nullable=False, default=RESEARCH_FOUND)
    error_msg = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    research = relationship("Research", back_populates="company", cascade="all, delete-orphan")
    contacts = relationship(
        "CompanyContact", back_populates="company", cascade="all, delete-orphan"
    )
    outreach = relationship("Outreach", back_populates="company", cascade="all, delete-orphan")

    # Named explicitly rather than via Column(index=True), which would emit
    # ix_companies_company_name: production provisions with create_all (see
    # scripts/start.sh) while migration 006 provisions with SQL, and the two
    # paths must produce byte-identical indexes.
    __table_args__ = (
        Index("companies_name_idx", "company_name"),
        Index("companies_status_idx", "research_status"),
    )


class CompanyContact(Base):
    """One emailable person at a company, from Hunter Domain Search.

    A pool rather than a single founder_email: a shared company pool would
    otherwise mean every user emails the same person.

    Ineligible contacts are stored too, so loosening the position filter later
    can re-classify stored rows instead of re-spending Hunter credits.
    """

    __tablename__ = "company_contacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    email = Column(String, nullable=False)
    first_name = Column(String)
    last_name = Column(String)
    position = Column(String)
    seniority = Column(String)
    department = Column(String)
    confidence = Column(Integer, nullable=False, default=0)  # Hunter 0-100
    is_founder = Column(Boolean, nullable=False, default=False)
    eligible = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    company = relationship("Company", back_populates="contacts")

    __table_args__ = (
        UniqueConstraint("company_id", "email", name="uq_contact_company_email"),
        # Partial: selection and pool queries only ever read eligible contacts,
        # so indexing the ineligible ones wastes space and write throughput.
        Index("company_contacts_eligible_idx", "company_id", postgresql_where=text("eligible")),
    )


class Outreach(Base):
    """One user's attempt to reach one company — the per-user half of the split.

    UNIQUE(user_id, company_id): a user targets a company at most once. Two
    different users targeting the same company is expected and fine.
    """

    __tablename__ = "outreach"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    company_id = Column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL, not CASCADE — see the model docstring in CompanyContact.
    contact_id = Column(UUID(as_uuid=True), ForeignKey("company_contacts.id", ondelete="SET NULL"))
    # Text, not String: migration 006 provisions this column as TEXT, and the
    # new partial indexes below compare it to a literal in their WHERE clause
    # (`status = 'approved'`). Postgres renders that comparison differently
    # for a TEXT column (no cast) than an unbounded VARCHAR one (an implicit
    # `(status)::text` cast survives in the index definition), so a
    # create_all-provisioned VARCHAR column would produce a byte-different
    # outreach_due_idx / outreach_sending_idx than the migration's TEXT
    # column — exactly what
    # test_create_all_and_the_migration_agree_on_indexes_and_constraints
    # exists to catch. Both are unbounded and identical in storage; only the
    # declared type differed.
    status = Column(Text, nullable=False, default=OUTREACH_QUEUED)
    scheduled_send_at = Column(DateTime(timezone=True))  # NULL = send immediately
    error_msg = Column(Text)
    # How many times drafting_recovery_task has reclaimed this row from a
    # stale 'drafting' claim back to 'queued'. Distinct from
    # dead_letter.retry_count (which counts human-initiated retries of an
    # already-dead-lettered row via POST /api/dlq/retry): this counts
    # crash-recovery attempts that happen BEFORE the row is ever dead-lettered,
    # so it must live on the row itself, not on a DLQ entry that doesn't exist
    # yet. Caps the reclaim loop — see MAX_DRAFTING_RECLAIMS.
    reclaim_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    company = relationship("Company", back_populates="outreach")
    contact = relationship("CompanyContact")
    drafts = relationship("Draft", back_populates="outreach", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("user_id", "company_id", name="uq_outreach_user_company"),
        Index("outreach_user_status_idx", "user_id", "status"),
        # For Stack 3's per-contact cap query: COUNT(*) WHERE contact_id = ?
        Index("outreach_contact_idx", "contact_id"),
        # Partial: the due-send scanner runs every 5 minutes forever, and
        # 'sent' rows will eventually dominate the table. Indexing every
        # status would grow this index without bound while only a sliver is
        # ever read. Also defined in migrations/010_send_cadence.sql (applied
        # via scripts/apply_storage.py) since create_all never ALTERs a table
        # that already exists in production — both copies must match so a
        # fresh test database (create_all) and production (migration) agree.
        Index(
            "outreach_due_idx", "scheduled_send_at", postgresql_where=text("status = 'approved'")
        ),
        # Rows reap_stuck_sends dead-letters once stale — see OUTREACH_SENDING.
        Index("outreach_sending_idx", "updated_at", postgresql_where=text("status = 'sending'")),
    )


class Research(Base):
    __tablename__ = "research"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"))
    tech_stack = Column(JSONB)
    recent_news = Column(Text)
    hook = Column(Text)
    raw_content = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    company = relationship("Company", back_populates="research")


class Draft(Base):
    __tablename__ = "drafts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    outreach_id = Column(
        UUID(as_uuid=True), ForeignKey("outreach.id", ondelete="CASCADE"), nullable=False
    )
    subject_line = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    version = Column(Integer, default=1)
    reviewer_notes = Column(Text)
    gmail_draft_id = Column(String)  # Gmail's draft resource ID, for later send/delete
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    outreach = relationship("Outreach", back_populates="drafts")

    __table_args__ = (Index("drafts_outreach_idx", "outreach_id"),)


class DeadLetter(Base):
    """One row per terminally-failed task.

    Written by handle_terminal_failure (the single failure choke point) so every
    permanent failure lands here with enough context to be re-dispatched.
    `stage` maps the row back to the worker that should retry it.

    Two nullable FKs with a CHECK that one is set. Research failures are
    company-level (nobody can email them); drafting/send failures are
    outreach-level (one user's problem). Collapsing both into one FK would lose
    that distinction.
    """

    __tablename__ = "dead_letter"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"))
    outreach_id = Column(UUID(as_uuid=True), ForeignKey("outreach.id", ondelete="CASCADE"))
    task_name = Column(String, nullable=False)
    stage = Column(String, nullable=False)  # research | drafting | logistics
    error_msg = Column(Text)
    retry_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_retried_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "company_id IS NOT NULL OR outreach_id IS NOT NULL", name="dead_letter_one_level"
        ),
        Index("dead_letter_stage_idx", "stage"),
    )


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


@contextlib.contextmanager
def get_sync_session():
    """Celery helper — yields a sync session per task."""
    with SyncSessionLocal() as session:
        yield session
