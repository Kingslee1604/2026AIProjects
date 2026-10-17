#!/usr/bin/env bash
# Security scanners used by CI (and available locally): Trivy, Syft, OSV-Scanner, pip-audit.
# Installed into ~/.local/bin with SHA-256 verification, like install-dev-tools.sh.
set -euo pipefail

TRIVY_VERSION="0.74.0"
SYFT_VERSION="1.52.0"
OSV_SCANNER_VERSION="2.6.0"
PIP_AUDIT_VERSION="2.10.1"

BIN="${HOME}/.local/bin"
mkdir -p "${BIN}"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

verify() { echo "$2  $1" | sha256sum --check --status || { echo "Checksum mismatch: $1" >&2; exit 1; }; }
sum_from() { curl -fsSL "$1" | awk -v a="$2" '$2 == a || $2 == "*"a {print $1}'; }

if ! "${BIN}/trivy" --version 2>/dev/null | grep -q "${TRIVY_VERSION}"; then
  base="https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}"
  asset="trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz"
  curl -fsSL -o "${tmp}/${asset}" "${base}/${asset}"
  verify "${tmp}/${asset}" "$(sum_from "${base}/trivy_${TRIVY_VERSION}_checksums.txt" "${asset}")"
  tar -xzf "${tmp}/${asset}" -C "${tmp}" trivy && install -m 0755 "${tmp}/trivy" "${BIN}/trivy"
fi

if ! "${BIN}/syft" version 2>/dev/null | grep -q "${SYFT_VERSION}"; then
  base="https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}"
  asset="syft_${SYFT_VERSION}_linux_amd64.tar.gz"
  curl -fsSL -o "${tmp}/${asset}" "${base}/${asset}"
  verify "${tmp}/${asset}" "$(sum_from "${base}/syft_${SYFT_VERSION}_checksums.txt" "${asset}")"
  tar -xzf "${tmp}/${asset}" -C "${tmp}" syft && install -m 0755 "${tmp}/syft" "${BIN}/syft"
fi

if ! "${BIN}/osv-scanner" --version 2>/dev/null | grep -q "${OSV_SCANNER_VERSION}"; then
  base="https://github.com/google/osv-scanner/releases/download/v${OSV_SCANNER_VERSION}"
  asset="osv-scanner_linux_amd64"
  curl -fsSL -o "${tmp}/${asset}" "${base}/${asset}"
  verify "${tmp}/${asset}" "$(sum_from "${base}/osv-scanner_SHA256SUMS" "${asset}")"
  install -m 0755 "${tmp}/${asset}" "${BIN}/osv-scanner"
fi

"${BIN}/uv" tool install --quiet "pip-audit==${PIP_AUDIT_VERSION}"

echo "trivy $("${BIN}/trivy" --version | head -1 | awk '{print $2}'), syft $("${BIN}/syft" version | awk '/^Version/ {print $2}'), osv-scanner $("${BIN}/osv-scanner" --version | awk '/version/ {print $NF; exit}'), pip-audit ${PIP_AUDIT_VERSION}"
