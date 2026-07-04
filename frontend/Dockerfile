# Synapse frontend — production image (R12/deploy)
#
# Multi-stage: build the Vite static bundle, then serve it with nginx.
# nginx serves the SPA shell and reverse-proxies the API path prefixes to the
# backend, so the browser keeps its RELATIVE API base (apiBase() -> "") exactly
# like the dev Vite proxy does (BACKEND_PROXY_TARGET). This keeps the image
# IP-agnostic: the backend URL is injected at container start, never baked in.
#
# Build context: frontend/
# Image: ghcr.io/<owner>/llm-wiki-synapse-frontend

# ---- build stage ----------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app

# Install deps from the lockfile (reproducible)
COPY package.json package-lock.json ./
RUN npm ci

# Build the static bundle. We call `vite build` directly (NOT `npm run build`,
# which also runs `tsc --noEmit`): esbuild transpiles without type-checking, so
# the image build is decoupled from type errors. Type-checking is enforced
# separately in CI (ci.yml `typecheck` stage), which is its proper gate.
COPY . .
RUN npx vite build

# ---- serve stage ----------------------------------------------------------
FROM nginx:1.27-alpine

# Static bundle produced by Vite
COPY --from=build /app/dist /usr/share/nginx/html

# nginx config template. The official image's entrypoint runs envsubst over
# /etc/nginx/templates/*.template at container start, expanding ${BACKEND_PROXY_TARGET}
# (and only real env vars — nginx runtime vars like $uri/$host are left intact),
# writing the result to /etc/nginx/conf.d/default.conf.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# Default backend location on the compose network; override per deployment.
ENV BACKEND_PROXY_TARGET=http://synapse-backend:8000

EXPOSE 80
