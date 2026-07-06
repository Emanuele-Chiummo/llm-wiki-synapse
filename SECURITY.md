# Security Policy

## Supported versions

Synapse is a solo, actively-developed project. Security fixes land on `main` and
ship in the next release. Only the latest release line receives fixes.

| Version | Supported          |
| ------- | ------------------ |
| 1.3.x   | :white_check_mark: |
| < 1.3   | :x:                |

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

- The backend is designed to run on a **trusted network** (Tailscale mesh /
  private LAN), not exposed directly to the public internet. Put a
  reverse proxy with authentication (or a Cloudflare Tunnel with Access) in
  front of any public endpoint.
- API keys and provider credentials belong in `.env` / environment variables —
  **never** commit them. `.env` is git-ignored; secret scanning
  (GitGuardian) runs on every push.
- The inference provider layer (F17) can execute against local (Ollama), API
  (Anthropic / OpenAI-compatible), or CLI (claude-agent-sdk) backends. CLI mode
  grants filesystem tools scoped to the vault — treat the vault path as trusted.

Reports that amount to "the backend has no auth when exposed publicly" describe
expected behavior for the documented threat model, not a vulnerability — but
concrete hardening suggestions are always welcome.
