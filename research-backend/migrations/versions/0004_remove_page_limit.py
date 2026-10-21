"""Remove the collection page ceiling while retaining nonnegative accounting."""
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade():
    # PostgreSQL's name for the single-column unnamed check in frozen revision 0001.
    op.drop_constraint("collections_used_pages_check", "collections", schema="app", type_="check")
    op.create_check_constraint("collections_used_pages_check", "collections", "used_pages >= 0", schema="app")


def downgrade():
    # Adding the original check fails atomically if the collection is now over 20 pages.
    op.drop_constraint("collections_used_pages_check", "collections", schema="app", type_="check")
    op.create_check_constraint("collections_used_pages_check", "collections",
                               "used_pages >= 0 AND used_pages <= 20", schema="app")
