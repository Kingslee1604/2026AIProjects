#!/usr/bin/env bash
# Runs once, when the Postgres data directory is first created (docker-entrypoint-initdb.d).
# Creates the login roles. Schemas, tables, grants, and RLS policies come from migrations.
#
#   agentforge_owner  - POSTGRES_USER; owns the schema and runs migrations (never used by the app)
#   agentforge_app    - API and worker; no superuser, no BYPASSRLS, owns nothing
#   agentforge_engine - the durable run engine; only the `engine` schema (architecture §7.4)
set -euo pipefail
: "${AGENTFORGE_APP_DB_PASSWORD:?must be set}"
: "${AGENTFORGE_ENGINE_DB_PASSWORD:?must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v db="$POSTGRES_DB" \
  -v app_pw="$AGENTFORGE_APP_DB_PASSWORD" \
  -v engine_pw="$AGENTFORGE_ENGINE_DB_PASSWORD" <<'SQL'
CREATE ROLE agentforge_app LOGIN PASSWORD :'app_pw'
  NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT;
CREATE ROLE agentforge_engine LOGIN PASSWORD :'engine_pw'
  NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT;

REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db" TO agentforge_app, agentforge_engine;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Extensions need superuser; create them here so migrations can run as a plain owner later.
CREATE EXTENSION IF NOT EXISTS vector;
SQL
