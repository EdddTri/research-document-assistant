"""Remove external identity mappings; retain users and application sessions.

The original migration remains immutable for installations already at revision 0001.
Downgrade recreates the schema only, not previously removed identity mappings.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"


def upgrade():
    op.drop_table("external_identities", schema="app")


def downgrade():
    op.create_table(
        "external_identities",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("user_id", sa.String, sa.ForeignKey("app.users.id"), nullable=False),
        sa.Column("issuer", sa.String, nullable=False),
        sa.Column("subject", sa.String, nullable=False),
        sa.UniqueConstraint("issuer", "subject"),
        schema="app",
    )
