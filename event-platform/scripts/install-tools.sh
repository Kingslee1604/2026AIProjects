#!/usr/bin/env bash
# Tools for `make check`, `make security` and CI, installed into ~/.local/bin (no sudo).
# Every download is verified against the publisher's SHA-256 checksum. Versions are pinned
# here (the same as agentforge/scripts/); bump them deliberately.
set -euo pipefail

UV_VERSION="0.12.18"
GITLEAKS_VERSION="8.30.1"
TRIVY_VERSION="0.74.0"
SYFT_VERSION="1.52.0"
OSV_SCANNER_VERSION="2.6.0"
PIP_AUDIT_VERSION="2.10.1"

if [[ "$(uname -s)-$(uname -m)" != "Linux-x86_64" ]]; then
  echo "This script supports Linux x86_64 (WSL2 Ubuntu, GitHub runners) only." >&2
  exit 1
fi

BIN="${HOME}/.local/bin"
mkdir -p "${BIN}"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

verify() { echo "$2  $1" | sha256sum --check --status || { echo "Checksum mismatch: $1" >&2; exit 1; }; }
sum_from() { curl -fsSL "$1" | awk -v a="$2" '$2 == a || $2 == "*"a {print $1}'; }
have() { "${BIN}/$1" "${@:3}" 2>/dev/null | grep -q -- "$2"; }  # have <tool> <version> <args>

if ! have uv "${UV_VERSION}" --version; then
  base="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}"
  asset="uv-x86_64-unknown-linux-gnu.tar.gz"
  curl -fsSL -o "${tmp}/${asset}" "${base}/${asset}"
  verify "${tmp}/${asset}" "$(curl -fsSL "${base}/${asset}.sha256" | awk '{print $1}')"
  tar -xzf "${tmp}/${asset}" -C "${tmp}"
  install -m 0755 "${tmp}/uv-x86_64-unknown-linux-gnu/uv" "${tmp}/uv-x86_64-unknown-linux-gnu/uvx" "${BIN}/"
fi

if ! have gitleaks "${GITLEAKS_VERSION}" --version; then
  base="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}"
  asset="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"
  curl -fsSL -o "${tmp}/${asset}" "${base}/${asset}"
  verify "${tmp}/${asset}" "$(sum_from "${base}/gitleaks_${GITLEAKS_VERSION}_checksums.txt" "${asset}")"
  tar -xzf "${tmp}/${asset}" -C "${tmp}" gitleaks && install -m 0755 "${tmp}/gitleaks" "${BIN}/gitleaks"
fi

if ! have trivy "${TRIVY_VERSION}" --version; then
  base="https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}"
  asset="trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz"
  curl -fsSL -o "${tmp}/${asset}" "${base}/${asset}"
  verify "${tmp}/${asset}" "$(sum_from "${base}/trivy_${TRIVY_VERSION}_checksums.txt" "${asset}")"
  tar -xzf "${tmp}/${asset}" -C "${tmp}" trivy && install -m 0755 "${tmp}/trivy" "${BIN}/trivy"
fi

if ! have syft "${SYFT_VERSION}" version; then
  base="https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}"
  asset="syft_${SYFT_VERSION}_linux_amd64.tar.gz"
  curl -fsSL -o "${tmp}/${asset}" "${base}/${asset}"
  verify "${tmp}/${asset}" "$(sum_from "${base}/syft_${SYFT_VERSION}_checksums.txt" "${asset}")"
  tar -xzf "${tmp}/${asset}" -C "${tmp}" syft && install -m 0755 "${tmp}/syft" "${BIN}/syft"
fi

if ! have osv-scanner "${OSV_SCANNER_VERSION}" --version; then
  base="https://github.com/google/osv-scanner/releases/download/v${OSV_SCANNER_VERSION}"
  asset="osv-scanner_linux_amd64"
  curl -fsSL -o "${tmp}/${asset}" "${base}/${asset}"
  verify "${tmp}/${asset}" "$(sum_from "${base}/osv-scanner_SHA256SUMS" "${asset}")"
  install -m 0755 "${tmp}/${asset}" "${BIN}/osv-scanner"
fi

"${BIN}/uv" tool install --quiet "pip-audit==${PIP_AUDIT_VERSION}"

echo "uv ${UV_VERSION}, gitleaks ${GITLEAKS_VERSION}, trivy ${TRIVY_VERSION}, syft ${SYFT_VERSION}," \
  "osv-scanner ${OSV_SCANNER_VERSION}, pip-audit ${PIP_AUDIT_VERSION}: ready in ${BIN}"
