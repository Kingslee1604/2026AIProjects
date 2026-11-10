#!/usr/bin/env bash
# Dependency, image and history scans, with an SBOM of the image. Used by CI and
# `make security`. Fails on known vulnerabilities that have a fix available.
#   deps    - pip-audit and OSV-Scanner over uv.lock (runtime and dev dependencies)
#   images  - Trivy (OS and library CVEs, HIGH/CRITICAL with a fix) and a Syft SBOM
#   history - gitleaks over the full git history
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="${HOME}/.local/bin:${PATH}"
mode="${1:-all}"
out="${SBOM_DIR:-build/sbom}"
IMAGE="eventplat:dev"  # compose.yaml builds every service from this one image

scan_deps() {
  echo "== pip-audit (uv.lock, all dependency groups)"
  reqs="$(mktemp)"
  uv export --frozen --all-groups --no-hashes --no-emit-project --format requirements-txt > "${reqs}"
  # The export is a complete, pinned set, so no resolution (and no pip) is needed.
  pip-audit --strict --progress-spinner off --requirement "${reqs}" --no-deps --disable-pip
  rm -f "${reqs}"
  echo "== OSV-Scanner (uv.lock)"
  osv-scanner scan source --lockfile uv.lock
}

scan_images() {
  mkdir -p "${out}"
  echo "== Trivy ${IMAGE}"
  trivy image --quiet --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 \
    "${IMAGE}"
  syft scan "docker:${IMAGE}" --quiet --output "spdx-json=${out}/eventplat.spdx.json"
  echo "   SBOM: ${out}/eventplat.spdx.json"
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
