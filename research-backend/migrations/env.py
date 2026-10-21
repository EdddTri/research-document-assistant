from alembic import context
from research_core.db import Base, engine
from research_core.config import settings
from research_core import models

if context.is_offline_mode():
    context.configure(url=settings().database_url_for("migration"), target_metadata=Base.metadata,
                      include_schemas=True, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine("migration").connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, include_schemas=True)
        with context.begin_transaction():
            context.run_migrations()
