"""SQLAlchemy models. Embeddings use pgvector on PostgreSQL and JSON on SQLite."""
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

EMBED_DIM = 256


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EmbeddingType(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            try:
                from pgvector.sqlalchemy import Vector
                return dialect.type_descriptor(Vector(EMBED_DIM))
            except ImportError:  # pragma: no cover
                pass
        return dialect.type_descriptor(JSON())


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    github_repo: Mapped[str | None] = mapped_column(String(300), nullable=True)  # owner/name
    local_git_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    discord_channel_ids: Mapped[list] = mapped_column(JSON, default=list)
    doc_urls: Mapped[list] = mapped_column(JSON, default=list)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint("project_id", "source", "external_id", name="uq_record_source"),
        Index("ix_record_project_kind", "project_id", "kind"),
        Index("ix_record_project_ts", "project_id", "timestamp"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(20))        # github | discord | docs
    kind: Mapped[str] = mapped_column(String(30))          # commit, issue, pr, review, message, doc, ...
    external_id: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(500), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(1000), default="")   # original reference
    author: Mapped[str] = mapped_column(String(200), default="")
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    category: Mapped[str] = mapped_column(String(30), default="other")
    category_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)  # permission / licensing metadata
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    embedding: Mapped[list | None] = mapped_column(EmbeddingType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (Index("ix_memory_project_cat", "project_id", "category"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(20))  # architecture|development|decision|problem|task
    key: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(400))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="active")
    origin: Mapped[str] = mapped_column(String(20), default="verified")  # verified | inferred
    version: Mapped[int] = mapped_column(Integer, default=1)
    superseded_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    source_record_ids: Mapped[list] = mapped_column(JSON, default=list)
    provenance: Mapped[list] = mapped_column(JSON, default=list)
    summary_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint("project_id", "src_key", "dst_key", "rel_type", name="uq_rel"),
        Index("ix_rel_src", "project_id", "src_key"),
        Index("ix_rel_dst", "project_id", "dst_key"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    src_key: Mapped[str] = mapped_column(String(300))   # record:12 | file:app.py
    dst_key: Mapped[str] = mapped_column(String(300))
    rel_type: Mapped[str] = mapped_column(String(40))
    origin: Mapped[str] = mapped_column(String(20), default="verified")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    evidence: Mapped[str] = mapped_column(String(500), default="")


class SyncState(Base):
    __tablename__ = "sync_state"
    __table_args__ = (UniqueConstraint("project_id", "source", name="uq_sync"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(20))
    cursor: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="idle")  # idle|running|ok|partial|error
    message: Mapped[str] = mapped_column(Text, default="")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AIUsage(Base):
    __tablename__ = "ai_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    role: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(200))
    task: Mapped[str] = mapped_column(String(60), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisResult(Base):
    __tablename__ = "analysis_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(40))
    question: Mapped[str] = mapped_column(String(500), default="")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
