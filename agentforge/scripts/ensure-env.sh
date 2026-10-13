#!/usr/bin/env bash
# Creates .env from .env.example, then fills any missing secret with a random value.
# Safe to run repeatedly: existing values are never changed.
set -euo pipefail
cd "$(dirname "$0")/.."

SECRETS=(POSTGRES_PASSWORD AGENTFORGE_APP_DB_PASSWORD AGENTFORGE_ENGINE_DB_PASSWORD)

umask 077
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env (git-ignored, readable only by you)."
fi
for key in "${SECRETS[@]}"; do
  if ! grep -qE "^${key}=.+" .env; then
    value="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    if grep -qE "^${key}=" .env; then
      sed -i "s|^${key}=.*|${key}=${value}|" .env
    else
      printf '%s=%s\n' "$key" "$value" >> .env
    fi
    echo "Generated ${key} in .env."
  fi
done
