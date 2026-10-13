# syntax=docker/dockerfile:1
# One image for the API and the worker (different commands). Build context: agentforge/.
# Base images are pinned by digest. They are exempt from the 14-day age rule because their
# rebuilds carry OS security fixes; update the tag and digest together.

FROM ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc AS uv

FROM python:3.12.15-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# Dependencies first (cached until the lockfile changes), then the package itself.
COPY pyproject.toml uv.lock ./
COPY apps/server/pyproject.toml apps/server/README.md apps/server/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace
COPY apps/server/src apps/server/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.12.15-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS runtime
RUN groupadd --system --gid 10001 agentforge \
 && useradd --system --uid 10001 --gid agentforge --no-create-home --shell /usr/sbin/nologin agentforge \
 && mkdir -p /data/blobs && chown agentforge:agentforge /data/blobs
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGENTFORGE_API_HOST=0.0.0.0 \
    AGENTFORGE_BLOB_ROOT=/data/blobs
USER agentforge
EXPOSE 8000
ENTRYPOINT ["agentforge"]
CMD ["api"]
