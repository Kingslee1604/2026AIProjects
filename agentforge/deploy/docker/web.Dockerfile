# syntax=docker/dockerfile:1
# Next.js web app (standalone output). Build context: agentforge/.
# The /api proxy target is fixed at build time (Next.js rewrites), so it is a build argument.

FROM node:24.21.0-trixie-slim@sha256:173f125896c3b47ddf056734c7ea789d04595a6a08769a8f78e0df642781fb66 AS deps
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0 NEXT_TELEMETRY_DISABLED=1
RUN corepack enable pnpm
WORKDIR /src
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile --filter @agentforge/web

FROM deps AS build
ARG AGENTFORGE_API_ORIGIN=http://api:8000
ENV AGENTFORGE_API_ORIGIN=${AGENTFORGE_API_ORIGIN}
COPY apps/web apps/web
RUN pnpm --filter @agentforge/web build

FROM node:24.21.0-trixie-slim@sha256:173f125896c3b47ddf056734c7ea789d04595a6a08769a8f78e0df642781fb66 AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
# The server needs only `node`. Removing npm and corepack drops their bundled dependencies
# (and their CVEs) from the runtime image.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
      /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack /opt/yarn-* \
      /usr/local/bin/yarn /usr/local/bin/yarnpkg
WORKDIR /app
COPY --from=build --chown=node:node /src/apps/web/.next/standalone ./
COPY --from=build --chown=node:node /src/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=node:node /src/apps/web/public ./apps/web/public
USER node
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
