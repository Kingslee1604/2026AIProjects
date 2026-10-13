#!/usr/bin/env bash
# Dependency, image, and history scans with SBOMs (security baseline S12). Used by CI and
# `make security`. Fails on known vulnerabilities that have a fix available.
#   deps   - pip-audit (Python lockfile) and OSV-Scanner (pnpm lockfile)
#   images - Trivy (OS and library CVEs, HIGH/CRITICAL with fixes) and Syft SBOMs
#   history - gitleaks over the full git history
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="${HOME}/.local/bin:${PATH}"
mode="${1:-all}"
out="${SBOM_DIR:-build/sbom}"

scan_deps() {
  echo "== pip-audit (Python runtime and dev dependencies from uv.lock)"
  reqs="$(mktemp)"
  uv export --frozen --all-groups --no-hashes --no-emit-workspace --format requirements-txt > "${reqs}"
  # The export is a complete, pinned set, so no resolution (and no pip) is needed.
  pip-audit --strict --progress-spinner off --requirement "${reqs}" --no-deps --disable-pip
  rm -f "${reqs}"
  echo "== OSV-Scanner (pnpm-lock.yaml)"
  osv-scanner scan source --lockfile pnpm-lock.yaml
}

scan_images() {
  mkdir -p "${out}"
  for image in agentforge-server:dev agentforge-web:dev; do
    echo "== Trivy ${image}"
    trivy image --quiet --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed \
      --exit-code 1 "${image}"
    name="${image%%:*}"
    syft scan "docker:${image}" --quiet --output "spdx-json=${out}/${name}.spdx.json"
    echo "   SBOM: ${out}/${name}.spdx.json"
  done
}

scan_history() {
  echo "== gitleaks (full history)"
  (cd .. && gitleaks git --no-banner --redact --config .gitleaks.toml .)
}

case "${mode}" in
  deps) scan_deps ;;
  images) scan_images ;;
  history) scan_history ;;
  all) scan_deps; scan_images; scan_history ;;
  *) echo "usage: $0 [deps|images|history|all]" >&2; exit 2 ;;
esac
