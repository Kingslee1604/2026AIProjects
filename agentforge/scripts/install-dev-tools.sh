#!/usr/bin/env bash
# Installs AgentForge's developer tools into ~/.local (no sudo, no system changes).
# Every download is verified against the publisher's SHA-256 checksum.
# Versions are pinned here; bump them deliberately and record the change.
set -euo pipefail

UV_VERSION="0.12.18"
NODE_VERSION="24.21.0"
PNPM_VERSION="12.7.0"
GITLEAKS_VERSION="8.30.1"
PRE_COMMIT_VERSION="4.6.2"

PREFIX="${HOME}/.local"
BIN="${PREFIX}/bin"
OPT="${PREFIX}/opt"
mkdir -p "${BIN}" "${OPT}"

if [[ "$(uname -s)-$(uname -m)" != "Linux-x86_64" ]]; then
  echo "This script supports Linux x86_64 (WSL2 Ubuntu) only." >&2
  exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

verify() { # verify <file> <expected-sha256>
  echo "$2  $1" | sha256sum --check --status || { echo "Checksum mismatch for $1" >&2; exit 1; }
}

have_version() { # have_version <cmd> <version-substring>
  command -v "$1" >/dev/null 2>&1 && "$1" --version 2>/dev/null | grep -q -- "$2"
}

# --- uv -------------------------------------------------------------------
if have_version "${BIN}/uv" "${UV_VERSION}"; then
  echo "uv ${UV_VERSION} already installed"
else
  base="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}"
  asset="uv-x86_64-unknown-linux-gnu.tar.gz"
  curl -fsSL -o "${tmp}/${asset}" "${base}/${asset}"
  sum="$(curl -fsSL "${base}/${asset}.sha256" | awk '{print $1}')"
  verify "${tmp}/${asset}" "${sum}"
  tar -xzf "${tmp}/${asset}" -C "${tmp}"
  install -m 0755 "${tmp}/uv-x86_64-unknown-linux-gnu/uv" "${tmp}/uv-x86_64-unknown-linux-gnu/uvx" "${BIN}/"
  echo "installed uv ${UV_VERSION}"
fi

# --- Node.js (LTS) ----------------------------------------------------------
node_dir="${OPT}/node-v${NODE_VERSION}-linux-x64"
if [[ -x "${node_dir}/bin/node" ]]; then
  echo "node ${NODE_VERSION} already installed"
else
  base="https://nodejs.org/dist/v${NODE_VERSION}"
  asset="node-v${NODE_VERSION}-linux-x64.tar.xz"
  curl -fsSL -o "${tmp}/${asset}" "${base}/${asset}"
  sum="$(curl -fsSL "${base}/SHASUMS256.txt" | awk -v a="${asset}" '$2 == a {print $1}')"
  verify "${tmp}/${asset}" "${sum}"
  tar -xJf "${tmp}/${asset}" -C "${OPT}"
  echo "installed node ${NODE_VERSION}"
fi
for tool in node npm npx corepack; do
  ln -sf "${node_dir}/bin/${tool}" "${BIN}/${tool}"
done

# --- pnpm (via corepack, pinned) ---------------------------------------------
export PATH="${BIN}:${PATH}"
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
corepack enable --install-directory "${BIN}" pnpm
corepack prepare "pnpm@${PNPM_VERSION}" --activate >/dev/null
echo "pnpm $(pnpm --version) ready"

# --- gitleaks ----------------------------------------------------------------
if have_version "${BIN}/gitleaks" "${GITLEAKS_VERSION}"; then
  echo "gitleaks ${GITLEAKS_VERSION} already installed"
else
  base="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}"
  asset="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"
  curl -fsSL -o "${tmp}/${asset}" "${base}/${asset}"
  sum="$(curl -fsSL "${base}/gitleaks_${GITLEAKS_VERSION}_checksums.txt" | awk -v a="${asset}" '$2 == a {print $1}')"
  verify "${tmp}/${asset}" "${sum}"
  tar -xzf "${tmp}/${asset}" -C "${tmp}" gitleaks
  install -m 0755 "${tmp}/gitleaks" "${BIN}/gitleaks"
  echo "installed gitleaks ${GITLEAKS_VERSION}"
fi

# --- pre-commit (isolated tool environment managed by uv) --------------------
"${BIN}/uv" tool install --quiet "pre-commit==${PRE_COMMIT_VERSION}"
echo "pre-commit $("${BIN}/pre-commit" --version | awk '{print $2}') ready"

case ":${PATH}:" in
  *":${BIN}:"*) ;;
  *) echo "NOTE: add ${BIN} to PATH (Ubuntu's default ~/.profile does this on next login)." ;;
esac
echo "Developer tools ready."
