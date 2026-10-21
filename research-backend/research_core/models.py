import time
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def uid():
    return str(uuid.uuid4())


def now():
    return int(time.time())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('reader','admin')"), {"schema": "app"})
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(default="reader")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    __table_args__ = {"schema": "app"}
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("app.users.id"), index=True)
    created_at: Mapped[int] = mapped_column(default=now)
    expires_at: Mapped[int]
    revoked: Mapped[bool] = mapped_column(default=False)
    csrf_hash: Mapped[str]
    user_agent: Mapped[str] = mapped_column(default="")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = {"schema": "app"}
    token_hash: Mapped[str] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("app.login_sessions.id"), index=True)
    used: Mapped[bool] = mapped_column(default=False)
    expires_at: Mapped[int]


class Collection(Base):
    __tablename__ = "collections"
    __table_args__ = (CheckConstraint("used_pages >= 0", name="collections_used_pages_check"), {"schema": "app"})
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    used_pages: Mapped[int] = mapped_column(default=0)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("sha256", "version"), {"schema": "app"})
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    title: Mapped[str]
    sha256: Mapped[str] = mapped_column(index=True)
    version: Mapped[int] = mapped_column(default=1)
    page_count: Mapped[int]
    status: Mapped[str] = mapped_column(default="queued", index=True)
    reserved: Mapped[bool] = mapped_column(default=True)
    storage_key: Mapped[str]
    replaces_id: Mapped[str | None] = mapped_column(ForeignKey("app.documents.id"))
    created_at: Mapped[int] = mapped_column(default=now)


class Job(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = {"schema": "app"}
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("app.documents.id"), index=True)
    status: Mapped[str] = mapped_column(default="queued")
    stage: Mapped[str] = mapped_column(default="waiting")
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    attempt_token: Mapped[str | None]
    lease_until: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[int] = mapped_column(default=now)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = {"schema": "app"}
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("app.users.id"), index=True)
    title: Mapped[str] = mapped_column(default="New conversation")
    created_at: Mapped[int] = mapped_column(default=now)
    revision: Mapped[int] = mapped_column(default=0)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = {"schema": "app"}
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("app.conversations.id"), index=True)
    role: Mapped[str]
    content: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[int] = mapped_column(default=now)
    sequence: Mapped[int]


class Content(Base):
    __tablename__ = "content"
    __table_args__ = (CheckConstraint("page >= 1"), {"schema": "knowledge"})
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("app.documents.id"), index=True)
    kind: Mapped[str]
    page: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    caption: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    bbox: Mapped[list | None] = mapped_column(JSON)
    page_width: Mapped[float] = mapped_column(Float)
    page_height: Mapped[float] = mapped_column(Float)
    crop_key: Mapped[str | None]
    method: Mapped[str]
    processing_version: Mapped[str]


class Chunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = {"schema": "knowledge"}
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    content_id: Mapped[str] = mapped_column(ForeignKey("knowledge.content.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("app.documents.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    # Preserve legacy vectors during model changes; retrieval filters model and dimensions.
    embedding: Mapped[list] = mapped_column(Vector().with_variant(JSON, "sqlite"))
    embedding_model: Mapped[str]
    processing_version: Mapped[str]


class ExtractionCache(Base):
    __tablename__ = "extraction_cache"
    __table_args__ = {"schema": "knowledge"}
    key: Mapped[str] = mapped_column(primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
