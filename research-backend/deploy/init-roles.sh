#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=backend_password="$BACKEND_DB_PASSWORD" \
  --set=worker_password="$WORKER_DB_PASSWORD" \
  --set=agent_password="$AGENT_DB_PASSWORD" <<'SQL'
CREATE ROLE research_backend LOGIN PASSWORD :'backend_password';
CREATE ROLE research_worker LOGIN PASSWORD :'worker_password';
CREATE ROLE research_agent LOGIN PASSWORD :'agent_password';
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
