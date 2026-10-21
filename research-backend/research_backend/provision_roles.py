from sqlalchemy import text
from research_core.db import engine


def main():
    statements = [
        "GRANT USAGE ON SCHEMA app, knowledge TO research_backend, research_worker, research_agent",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO research_backend",
        "GRANT SELECT ON ALL TABLES IN SCHEMA knowledge TO research_backend",
        "GRANT SELECT ON app.documents TO research_agent",
        "GRANT SELECT ON knowledge.content, knowledge.document_chunks TO research_agent",
        "GRANT SELECT, UPDATE ON app.documents, app.ingestion_jobs, app.collections TO research_worker",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA knowledge TO research_worker",
    ]
    with engine("migration").begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


if __name__ == "__main__":
    main()
