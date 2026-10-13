#!/usr/bin/env bash
# Creates .env from .env.example with a random database password, once. Never overwrites.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  exit 0
fi
password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
umask 077
sed "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${password}|" .env.example > .env
echo "Created .env with a random POSTGRES_PASSWORD (git-ignored, readable only by you)."
