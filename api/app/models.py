"""App tables (schema `app`). LangGraph checkpoints and Mem0 live in their own schemas and manage their own tables."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import APP_SCHEMA

# Stable constraint names so Alembic diffs stay clean.
NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

VISIBILITY = ("private", "org")


class Base(DeclarativeBase):
    metadata = MetaData(schema=APP_SCHEMA, naming_convention=NAMING)


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


def _visibility() -> Mapped[str]:
    return mapped_column(String(10), nullable=False, default="private", server_default="private")


class Org(Base):
    """acme.com -> slug 'acme'. gmail.com users get a personal org 'user-<shortid>'. Slug never changes."""

    __tablename__ = "orgs"
    id: Mapped[uuid.UUID] = _pk()
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email_domain: Mapped[str | None] = mapped_column(String(255), unique=True)  # NULL for personal orgs
    is_personal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = _created()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)  # lowercased; also Mem0 user_id
    short_id: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)  # used in corpus/org names
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    picture_url: Mapped[str | None] = mapped_column(Text)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"), nullable=False, index=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = _created()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    org: Mapped[Org] = relationship(lazy="joined")

    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)


class UserCredential(Base):
    """Google refresh token, Fernet-encrypted. Never returned to the browser."""

    __tablename__ = "user_credentials"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    refresh_token_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = _updated()


class Session(Base):
    """API session for the web app (opaque bearer token; only its SHA-256 is stored). Sign-out deletes the row."""

    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                                               index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(400))

    user: Mapped[User] = relationship(lazy="joined")


class Dataset(Base):
    """A BigQuery dataset a user registered. The MCP server only lets a user query datasets they can see here."""

    __tablename__ = "datasets"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"), nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    location: Mapped[str] = mapped_column(String(64), nullable=False)
    visibility: Mapped[str] = _visibility()
    description: Mapped[str | None] = mapped_column(Text)
    schema_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    tables: Mapped[list[DatasetTable]] = relationship(back_populates="dataset", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("owner_id", "project_id", "dataset_id"),
        CheckConstraint("visibility IN ('private', 'org')", name="visibility"),
    )

    @property
    def fq_name(self) -> str:
        return f"{self.project_id}.{self.dataset_id}"


class DatasetTable(Base):
    """Schema snapshot taken as the user. `columns`: [{name, type, mode, description, description_override}]."""

    __tablename__ = "dataset_tables"
    id: Mapped[uuid.UUID] = _pk()
    dataset_pk: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False)
    table_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    num_rows: Mapped[int | None] = mapped_column(BigInteger)
    partition_column: Mapped[str | None] = mapped_column(String(300))
    cluster_columns: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    columns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    refreshed_at: Mapped[datetime] = _created()

    dataset: Mapped[Dataset] = relationship(back_populates="tables")

    __table_args__ = (UniqueConstraint("dataset_pk", "table_name"),)


class DatasetNote(Base):
    """Business rules for a dataset (e.g. how revenue is defined); injected into the system prompt."""

    __tablename__ = "dataset_notes"
    id: Mapped[uuid.UUID] = _pk()
    dataset_pk: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False,
                                                  index=True)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    visibility: Mapped[str] = _visibility()
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    __table_args__ = (CheckConstraint("visibility IN ('private', 'org')", name="visibility"),)


class RagCorpus(Base):
    """Vertex RAG corpora, created lazily: org-<slug>-verified-sql / user-<shortid>-verified-sql."""

    __tablename__ = "rag_corpora"
    id: Mapped[uuid.UUID] = _pk()
    scope: Mapped[str] = mapped_column(String(10), nullable=False)  # org | user
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    display_name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    corpus_name: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)  # projects/../ragCorpora/..
    created_at: Mapped[datetime] = _created()

    __table_args__ = (CheckConstraint("scope IN ('org', 'user')", name="scope"),)


class Example(Base):
    """Verified question -> SQL. Metadata lives here so lists/filters never hit RAG."""

    __tablename__ = "examples"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    dataset_pk: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False)
    visibility: Mapped[str] = _visibility()
    question: Mapped[str] = mapped_column(Text, nullable=False)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    gcs_uri: Mapped[str | None] = mapped_column(Text)
    corpus_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rag_corpora.id"))
    rag_file_name: Mapped[str | None] = mapped_column(String(300))  # projects/../ragFiles/.. (for delete)
    verified_at: Mapped[datetime] = _created()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    __table_args__ = (
        CheckConstraint("visibility IN ('private', 'org')", name="visibility"),
        Index("ix_examples_org_dataset", "org_id", "dataset_pk"),
    )


class Chat(Base):
    """Chat metadata. Messages are in the LangGraph checkpointer under thread_id '<email>:<chat id>'."""

    __tablename__ = "chats"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                                               index=True)
    dataset_pk: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("datasets.id", ondelete="SET NULL"))
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="New chat", server_default="New chat")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class QueryLog(Base):
    """Every executed BigQuery job: per-user daily byte budget and the admin cost view."""

    __tablename__ = "query_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    dataset_pk: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("datasets.id", ondelete="SET NULL"))
    job_id: Mapped[str] = mapped_column(String(300), nullable=False)
    model: Mapped[str | None] = mapped_column(String(200))
    gb_processed: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = _created()

    __table_args__ = (Index("ix_query_log_user_created", "user_id", "created_at"),)
