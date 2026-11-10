#!/usr/bin/env bash
# Creates .env (git-ignored, readable only by you) with a random database password.
# Safe to run repeatedly: an existing value is never changed, because Postgres keeps the
# password it was first started with in its data volume.
set -euo pipefail
cd "$(dirname "$0")/.."

umask 077
touch .env
if ! grep -qE '^POSTGRES_PASSWORD=.+' .env; then
  value="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  sed -i '/^POSTGRES_PASSWORD=/d' .env
  printf 'POSTGRES_PASSWORD=%s\n' "$value" >> .env
  echo "Generated POSTGRES_PASSWORD in .env."
fi
