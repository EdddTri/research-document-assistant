"""Initial coordinated app/knowledge schema."""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "0001"
down_revision = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS app")
    op.execute("CREATE SCHEMA IF NOT EXISTS knowledge")
    # Frozen table definitions: this migration does not import mutable ORM metadata.
    op.create_table("users", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), unique=True, nullable=False),
        sa.Column("password_hash", sa.Text), sa.Column("role", sa.String, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.CheckConstraint("role IN ('reader','admin')"), schema="app")
    op.create_table("external_identities", sa.Column("id", sa.String, primary_key=True),
        sa.Column("user_id", sa.String, sa.ForeignKey("app.users.id"), nullable=False),
        sa.Column("issuer", sa.String, nullable=False), sa.Column("subject", sa.String, nullable=False),
        sa.UniqueConstraint("issuer", "subject"), schema="app")
    op.create_table("login_sessions", sa.Column("id", sa.String, primary_key=True),
        sa.Column("user_id", sa.String, sa.ForeignKey("app.users.id"), nullable=False, index=True),
        sa.Column("created_at", sa.Integer, nullable=False), sa.Column("expires_at", sa.Integer, nullable=False),
        sa.Column("revoked", sa.Boolean, nullable=False), sa.Column("csrf_hash", sa.String, nullable=False),
        sa.Column("user_agent", sa.String, nullable=False), schema="app")
    op.create_table("refresh_tokens", sa.Column("token_hash", sa.String, primary_key=True),
        sa.Column("session_id", sa.String, sa.ForeignKey("app.login_sessions.id"), nullable=False, index=True),
        sa.Column("used", sa.Boolean, nullable=False), sa.Column("expires_at", sa.Integer, nullable=False), schema="app")
    op.create_table("collections", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("used_pages", sa.Integer, nullable=False),
        sa.CheckConstraint("used_pages >= 0 AND used_pages <= 20"), schema="app")
    op.execute("INSERT INTO app.collections (id, used_pages) VALUES (1, 0)")
    op.create_table("documents", sa.Column("id", sa.String, primary_key=True),
        sa.Column("title", sa.String, nullable=False), sa.Column("sha256", sa.String, nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False), sa.Column("page_count", sa.Integer, nullable=False),
        sa.Column("status", sa.String, nullable=False, index=True), sa.Column("reserved", sa.Boolean, nullable=False),
        sa.Column("storage_key", sa.String, nullable=False),
        sa.Column("replaces_id", sa.String, sa.ForeignKey("app.documents.id")),
        sa.Column("created_at", sa.Integer, nullable=False), sa.UniqueConstraint("sha256", "version"), schema="app")
    op.create_table("ingestion_jobs", sa.Column("id", sa.String, primary_key=True),
        sa.Column("document_id", sa.String, sa.ForeignKey("app.documents.id"), nullable=False, index=True),
        sa.Column("status", sa.String, nullable=False), sa.Column("stage", sa.String, nullable=False),
        sa.Column("error", sa.Text), sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("attempt_token", sa.String), sa.Column("lease_until", sa.Integer, nullable=False),
        sa.Column("created_at", sa.Integer, nullable=False), schema="app")
    op.create_table("outbox", sa.Column("id", sa.String, primary_key=True),
        sa.Column("job_id", sa.String, sa.ForeignKey("app.ingestion_jobs.id"), nullable=False, unique=True),
        sa.Column("published_at", sa.Integer, nullable=False), schema="app")
    op.create_table("conversations", sa.Column("id", sa.String, primary_key=True),
        sa.Column("user_id", sa.String, sa.ForeignKey("app.users.id"), nullable=False, index=True),
        sa.Column("title", sa.String, nullable=False), sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False), schema="app")
    op.create_table("messages", sa.Column("id", sa.String, primary_key=True),
        sa.Column("conversation_id", sa.String, sa.ForeignKey("app.conversations.id"), nullable=False, index=True),
        sa.Column("role", sa.String, nullable=False), sa.Column("content", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Integer, nullable=False), sa.Column("sequence", sa.Integer, nullable=False), schema="app")
    op.create_table("content", sa.Column("id", sa.String, primary_key=True),
        sa.Column("document_id", sa.String, sa.ForeignKey("app.documents.id"), nullable=False, index=True),
        sa.Column("kind", sa.String, nullable=False), sa.Column("page", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False), sa.Column("caption", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False), sa.Column("data", sa.JSON, nullable=False),
        sa.Column("bbox", sa.JSON), sa.Column("page_width", sa.Float, nullable=False),
        sa.Column("page_height", sa.Float, nullable=False), sa.Column("crop_key", sa.String),
        sa.Column("method", sa.String, nullable=False), sa.Column("processing_version", sa.String, nullable=False),
        sa.CheckConstraint("page >= 1"), schema="knowledge")
    op.create_table("document_chunks", sa.Column("id", sa.String, primary_key=True),
        sa.Column("content_id", sa.String, sa.ForeignKey("knowledge.content.id"), nullable=False, index=True),
        sa.Column("document_id", sa.String, sa.ForeignKey("app.documents.id"), nullable=False, index=True),
        sa.Column("text", sa.Text, nullable=False), sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("embedding_model", sa.String, nullable=False),
        sa.Column("processing_version", sa.String, nullable=False), schema="knowledge")
    op.create_table("extraction_cache", sa.Column("key", sa.String, primary_key=True),
        sa.Column("data", sa.JSON, nullable=False), schema="knowledge")
    op.execute("CREATE INDEX chunk_text_search ON knowledge.document_chunks USING gin (to_tsvector('english', text))")
    # Twenty pages are small enough for exact vector search; no approximate index.


def downgrade():
    for schema, tables in [
        ("knowledge", ["extraction_cache", "document_chunks", "content"]),
        ("app", ["messages", "conversations", "outbox", "ingestion_jobs", "documents", "collections",
                 "refresh_tokens", "login_sessions", "external_identities", "users"]),
    ]:
        for table in tables:
            op.drop_table(table, schema=schema)
