# Security Policy

## Supported versions

Synapse is a solo, actively-developed project. Security fixes land on `main` and
ship in the next release. Only the latest release line receives fixes.

| Version | Supported          |
| ------- | ------------------ |
| 1.6.x   | :white_check_mark: |
| < 1.6   | :x:                |

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report privately through either channel:

1. **GitHub Security Advisories** (preferred) — use the
   [*Report a vulnerability*](https://github.com/Emanuele-Chiummo/llm-wiki-synapse/security/advisories/new)
   button on the repository's **Security** tab. This keeps the report private and
   lets us collaborate on a fix and coordinated disclosure.
2. **Email** — **emanuelechiummo@outlook.it** with the subject line
   `[SECURITY] Synapse`.

Please include:

- A description of the vulnerability and its impact
- Steps to reproduce (proof-of-concept, affected endpoint/component, version)
- Any suggested remediation, if you have one

## What to expect

- **Acknowledgement** within **7 days**.
- An initial assessment and severity triage shortly after.
- Progress updates as a fix is developed; credit in the release notes and the
  security advisory unless you prefer to remain anonymous.

Please give us a reasonable window to release a fix before any public
disclosure.

## Scope & self-hosting notes

Synapse is **self-hosted**. Operators are responsible for the security of their
own deployment. When reporting, keep in mind the intended threat model:

- `SYNAPSE_DEPLOYMENT_MODE=local` is the backward-compatible mode for loopback development and
  trusted single-machine use. It permits an empty `SYNAPSE_AUTH_TOKEN` and must not be used for a
  backend reachable by other machines.
- Every LAN, Tailscale, tunnel, reverse-proxy or hosted backend must use
  `SYNAPSE_DEPLOYMENT_MODE=server`. Server mode fails startup unless the env-only
  `SYNAPSE_AUTH_TOKEN` is present, contains at least 32 characters, contains no whitespace and has
  at least eight distinct characters. Generate it with `openssl rand -hex 32`; do not use a human
  password or placeholder.
- Do not expose the backend directly to the public internet. Put an authenticated reverse proxy
  or Cloudflare Tunnel with Access in front of it. Edge authentication complements, rather than
  replaces, the application token in server mode.
- Public health is limited to `GET`/`HEAD /health/live` and the existing `/status` connection
  probe. `/health/detailed` exposes operational diagnostics and requires the REST bearer token
  whenever auth is enabled.
- API keys and provider credentials belong in `.env` / environment variables —
  **never** commit them. `.env` is git-ignored; secret scanning
  (GitGuardian) runs on every push.
- The inference provider layer (F17) can execute against local (Ollama), API
  (Anthropic / OpenAI-compatible), or CLI (claude-agent-sdk) backends. CLI mode
  grants filesystem tools scoped to the vault — treat the vault path as trusted.

An unauthenticated API in explicitly selected `local` mode is expected only inside its documented
single-machine trust boundary. Authentication bypasses in `server` mode, disclosure through
protected diagnostics, or a way to start `server` mode with a missing/weak token are in scope for
security reports. Deployment and migration instructions are in
[`docs/DEPLOY.md`](docs/DEPLOY.md#security); the rationale is recorded in
[`ADR-0075`](docs/adr/0075-deployment-mode-auth-health-boundary.md).
