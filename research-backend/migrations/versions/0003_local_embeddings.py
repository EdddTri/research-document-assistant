"""Allow BGE-M3 vectors while preserving legacy embeddings and all source data.

Model and dimension filters isolate embedding spaces during retrieval. Reprocess
existing PDFs to make them searchable with BGE-M3. No vectors are converted.
"""
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade():
    op.execute("ALTER TABLE knowledge.document_chunks ALTER COLUMN embedding TYPE vector")


def downgrade():
    # PostgreSQL refuses this cast if any non-1536-dimensional vectors remain.
    # This deliberately fails safely instead of deleting or padding new vectors.
    op.execute("ALTER TABLE knowledge.document_chunks ALTER COLUMN embedding TYPE vector(1536)")
