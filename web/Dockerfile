# syntax=docker/dockerfile:1

FROM node:22.21.0-bookworm-slim AS deps
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@10.15.1 --activate
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

FROM deps AS build
COPY . .
ARG NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN pnpm build

FROM node:22.21.0-bookworm-slim AS production
WORKDIR /app
RUN groupadd --system appgroup && useradd --system --gid appgroup --home-dir /app appuser
COPY --from=build --chown=appuser:appgroup /app/.next/standalone ./
COPY --from=build --chown=appuser:appgroup /app/.next/static ./.next/static
COPY --from=build --chown=appuser:appgroup /app/public ./public
USER appuser
EXPOSE 3000
ENV HOSTNAME=0.0.0.0 \
    PORT=3000
CMD ["node", "server.js"]
