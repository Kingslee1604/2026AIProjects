#!/usr/bin/env bash
# `make doctor`: checks this machine can run AgentForge and says how to fix what isn't right.
# ✖ = blocks the stack (exit 1), ⚠ = works but degraded or needed later, ✔ = fine.
set -uo pipefail
cd "$(dirname "$0")/.."

BUSYBOX="busybox:1.37.0@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e"
failures=0
ok()   { printf '  \033[32m✔\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m⚠\033[0m %s\n' "$1"; [[ $# -gt 1 ]] && printf '      → %s\n' "$2"; }
fail() { printf '  \033[31m✖\033[0m %s\n' "$1"; [[ $# -gt 1 ]] && printf '      → %s\n' "$2"; failures=$((failures + 1)); }
section() { printf '\n%s\n' "$1"; }
is_wsl() { grep -qi microsoft /proc/version 2>/dev/null; }

section "Location"
here="$(pwd)"
if [[ "$here" == /mnt/* ]]; then
  fail "Repository is on the Windows filesystem ($here)" "Clone into the Linux filesystem, e.g. ~/src/2026AIProjects"
elif [[ "$here" == *OneDrive* ]]; then
  fail "Repository is inside a OneDrive folder" "Move it out of OneDrive; sync breaks file watchers and locks"
else
  ok "Repository on the Linux filesystem ($here)"
fi

section "Developer tools"
for tool in uv node pnpm gitleaks pre-commit; do
  if command -v "$tool" >/dev/null 2>&1; then
    ok "$tool $("$tool" --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)"
  else
    fail "$tool not found" "Run: make tools  (installs pinned versions into ~/.local/bin)"
  fi
done

section "Docker"
if ! command -v docker >/dev/null 2>&1; then
  fail "docker not found" "Install Docker Desktop and enable WSL Integration for this distro (Settings → Resources → WSL Integration)"
elif ! docker info >/dev/null 2>&1; then
  fail "Docker daemon not reachable" "Start Docker Desktop; on WSL also enable WSL Integration for this distro"
else
  ok "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null), Compose $(docker compose version --short 2>/dev/null)"
fi

section "Memory and disk"
mem_gb=$(awk '/MemTotal/ {printf "%.1f", $2/1048576}' /proc/meminfo)
if awk -v m="$mem_gb" 'BEGIN { exit !(m < 6) }'; then
  warn "${mem_gb} GB RAM visible to Linux" "AgentForge with a 3–4B model needs about 6 GB here; raise the WSL memory limit"
else
  ok "${mem_gb} GB RAM visible to Linux"
fi
if is_wsl; then
  win_profile="$(cmd.exe /c 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r')"
  wslconfig="$(wslpath "$win_profile" 2>/dev/null)/.wslconfig"
  if [[ -n "$win_profile" && -f "$wslconfig" ]] && grep -qiE '^\s*memory\s*=' "$wslconfig"; then
    ok "WSL memory cap set ($(grep -iE '^\s*memory\s*=' "$wslconfig" | head -1 | tr -d ' '))"
  else
    warn "No WSL memory cap (.wslconfig)" "WSL may use up to half of RAM and starve Ollama on Windows; see docs/guides (memory=6GB, swap=4GB)"
  fi
fi
free_gb=$(df -Pk . | awk 'NR==2 {printf "%.0f", $4/1048576}')
if (( free_gb < 15 )); then
  warn "${free_gb} GB free disk" "Images, database, and models need about 15 GB"
else
  ok "${free_gb} GB free disk"
fi

section "Ollama (as the containers will reach it)"
ollama_url="${AGENTFORGE_OLLAMA_BASE_URL:-http://host.docker.internal:11434}"
[[ -f .env ]] && env_url="$(grep -E '^AGENTFORGE_OLLAMA_BASE_URL=' .env | cut -d= -f2-)" && [[ -n "$env_url" ]] && ollama_url="$env_url"
if docker info >/dev/null 2>&1; then
  tags_json="$(docker run --rm --add-host=host.docker.internal:host-gateway "$BUSYBOX" \
    wget -qO- -T 5 "${ollama_url}/api/tags" 2>/dev/null)"
  if [[ -z "$tags_json" ]]; then
    warn "Ollama not reachable at $ollama_url" "Start Ollama (Windows: run the Ollama app). Needed from milestone M2 on"
  else
    ok "Ollama reachable at $ollama_url"
    installed="$(python3 -c 'import json,sys; print("\n".join(m["name"] for m in json.load(sys.stdin)["models"]))' <<<"$tags_json")"
    for wanted in $(awk '/^ *default:/ {print $2}' config/models.yaml); do
      if grep -qxF "$wanted" <<<"$installed"; then
        ok "model $wanted installed"
      else
        warn "model $wanted not installed" "Run: ollama pull $wanted  (needed from milestone M2 on)"
      fi
    done
  fi
else
  warn "Skipped Ollama check (Docker not available)"
fi

section "Ports (127.0.0.1)"
for port in "${API_PORT:-8000}" "${POSTGRES_PORT:-5432}"; do
  owner="$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -E "127\.0\.0\.1:${port}->" | awk '{print $1}')"
  if [[ -n "$owner" ]]; then
    ok "port $port used by $owner"
  elif (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    fail "port $port is taken by another program" "Stop it, or set a different port in .env (API_PORT / POSTGRES_PORT)"
  else
    ok "port $port free"
  fi
done

section "Configuration"
if [[ -f .env ]]; then ok ".env present"; else warn ".env missing" "Created automatically by: make up"; fi

printf '\n'
if (( failures > 0 )); then
  printf '\033[31m%d problem(s) block the stack.\033[0m\n' "$failures"
  exit 1
fi
printf '\033[32mReady.\033[0m Warnings (⚠) do not block "make up".\n'
