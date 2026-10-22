#!/usr/bin/env bash
# R1-49: measure the running stack and write bench/results/<date>-<machine>/.
#   make bench [BENCH_MACHINE=inspiron-3520]     (the stack must be up: make up)
# Keep the laptop on mains power: on battery, Windows throttles the CPU.
# Takes about an hour on the CPU-only reference laptop (mostly the 40-question run).
set -euo pipefail
cd "$(dirname "$0")/.."

machine="${BENCH_MACHINE:-$(hostname)}"
out="bench/results/$(date +%F)-${machine}"
mkdir -p "$out"
echo "Writing to $out"

# --- Hardware and software manifest ----------------------------------------------------
windows_info='{}'
if command -v powershell.exe >/dev/null; then
  windows_info="$(powershell.exe -NoProfile -Command '
    $c = Get-CimInstance Win32_Processor; $s = Get-CimInstance Win32_ComputerSystem
    $b = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue
    $o = Get-CimInstance Win32_OperatingSystem
    @{ cpu = $c.Name; cores = $c.NumberOfCores; threads = $c.NumberOfLogicalProcessors;
       ram_gb = [math]::Round($s.TotalPhysicalMemory / 1GB, 1); model = $s.Model;
       os = "$($o.Caption) $($o.BuildNumber)";
       on_mains_power = ($null -eq $b -or $b.BatteryStatus -eq 2) } | ConvertTo-Json -Compress' |
    tr -d '\r')"
fi
ollama_info="$(docker compose exec -T api python -c '
import json, urllib.request
base = "http://host.docker.internal:11434"
get = lambda p: json.load(urllib.request.urlopen(base + p, timeout=10))
print(json.dumps({"version": get("/api/version").get("version"),
  "models": {m["name"]: m["digest"][:12] for m in get("/api/tags")["models"]}}))' 2>/dev/null ||
  echo '{}')"
python3 - "$out/manifest.json" "$windows_info" "$ollama_info" <<'EOF'
import json, os, platform, subprocess, sys
def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()
manifest = {
    "date_utc": sh("date -u +%FT%TZ"),
    "commit": sh("git rev-parse --short HEAD") + ("-dirty" if sh("git status --porcelain -- apps") else ""),
    "windows": json.loads(sys.argv[2] or "{}"),
    "wsl": {"kernel": platform.release(), "memory_total_mb": int(sh("free -m | awk '/Mem:/{print $2}'")),
            "cpus": os.cpu_count()},
    "docker": sh("docker version --format '{{.Server.Version}}'"),
    "ollama": json.loads(sys.argv[3] or "{}"),
    "settings": {"model_provider": "auto (real part), fake (enqueue part)",
                 "chat_model": "default (ollama:qwen3:4b)", "embedding_model": "default"},
}
json.dump(manifest, open(sys.argv[1], "w"), indent=2)
EOF

# --- Memory sampling: Windows side (WSL VM, Ollama) and inside WSL ------------------------
stop="$out/.stop"; rm -f "$stop"
if command -v powershell.exe >/dev/null; then
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w bench/memory-sampler.ps1)" \
    -Out "$(wslpath -w "$out/memory_windows.csv")" -StopFile "$(wslpath -w "$stop")" &
fi
( echo "epoch_ms,wsl_used_mb"; while [[ ! -f "$stop" ]]; do
    echo "$(date +%s%3N),$(free -m | awk '/Mem:/{print $3}')"; sleep 5; done ) > "$out/memory_wsl.csv" &
trap 'touch "$stop"' EXIT

# --- A user for the measurements --------------------------------------------------------
export BENCH_EMAIL="bench-$(date +%s)@example.test"
BENCH_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
export BENCH_PASSWORD
printf '%s\n' "$BENCH_PASSWORD" | docker compose run --rm --no-deps -T api user create \
  --email "$BENCH_EMAIL" --role admin --password-stdin >/dev/null

# --- Real model -------------------------------------------------------------------------
uv run --frozen python bench/measure.py real "$out"

# --- Fake model: engine enqueue -> start, with the run rate limit raised -----------------
override="$(mktemp --suffix=.yaml)"
cat > "$override" <<'EOF'
services:
  api:
    environment: { AGENTFORGE_RATE_RUNS_PER_MINUTE: "1000" }
EOF
AGENTFORGE_MODEL_PROVIDER=fake docker compose -f compose.yaml -f "$override" up -d --wait >/dev/null
uv run --frozen python bench/measure.py fake "$out" || echo "fake part failed"
rm -f "$override"
docker compose up -d --wait >/dev/null   # back to the configured models
touch "$stop"; sleep 6

uv run --frozen python bench/measure.py summary "$out"
echo "Done: $out/summary.md"
