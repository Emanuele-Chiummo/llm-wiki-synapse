# Synapse Web Clipper — Chrome Extension (MV3)

A Chrome Manifest V3 extension that clips web articles to your Synapse knowledge base.

## Permissions

### Required permissions:

- **`activeTab`** — Read the active tab's URL and title for context.
- **`scripting`** — Inject Readability and Turndown libraries into the page to extract article content.
- **`storage`** — Persist the Synapse backend URL and clip token in `chrome.storage.sync` (encrypted at rest by Chrome).

### Host permissions:

The extension requires network access to your Synapse backend:

- **`http://localhost:*/*`** — For local development (`http://localhost:8000`).
- **`http://host.docker.internal:*/*`** — For Docker containers on macOS/Windows (Docker Desktop special hostname).
- **`https://*/*`** — For remote Synapse deployments over HTTPS (e.g., Cloudflare Tunnel, Tailscale).

**Note:** If your Synapse runs on a non-standard port (e.g., `http://localhost:9000`), the wildcards above cover it. If it runs on a custom hostname on your LAN, you may need to add an additional host permission in `manifest.json` (e.g., `"http://synapse.local:*/*"`).

## Building / Installing

### Development (unpacked):

1. Clone this repository or navigate to `extension/` in the Synapse repository root.
2. Open Chrome and go to `chrome://extensions/`.
3. Enable **Developer mode** (toggle in the top-right corner).
4. Click **Load unpacked** and select the `extension/` directory.
5. The Synapse icon appears in the browser toolbar.

### Configuration:

1. Click the Synapse icon in the toolbar and select **Options** (or right-click and choose **Extension options**).
2. Enter your Synapse backend URL (e.g., `http://localhost:8000`).
3. Enter the **Clip Token** — retrieve it from Synapse settings or generate one from your environment (`CLIP_TOKEN`).
4. Click **Save**.
5. Optionally, click **Test Connection** to verify the backend is reachable.

### Production (Store):

Publication on the Chrome Web Store is planned for a future release. For now, use the unpacked installation method above.

## Files

| File | Purpose |
|------|---------|
| `manifest.json` | MV3 manifest (permissions, icons, entry points). |
| `popup.html` | UI shown when clicking the extension icon. |
| `popup.js` | Extracts article via Readability + Turndown; sends to `/clip` endpoint. |
| `options.html` | Settings page (backend URL + clip token). |
| `options.js` | Manages persistent settings in `chrome.storage.sync`. |
| `icons/` | Extension icons (16, 32, 48, 128 px). |
| `vendor/Readability.js` | Mozilla Readability library (extracts article content from HTML). |
| `vendor/turndown.js` | Turndown library (converts HTML to Markdown). |

## Security

- **Token storage:** The clip token is stored in `chrome.storage.sync`, which Chrome encrypts at rest. It is never displayed in full after save (password input stays masked).
- **Origin validation:** The Synapse backend validates the `Origin` header against a configurable allowlist (`CLIP_ALLOWED_ORIGINS`). The extension ID is shown in the options page; add it to your Synapse environment.
- **Path safety:** The backend uses safe path joins and only writes to `vault/raw/sources/`.
- **Body size limit:** The backend caps requests at 2 MB (configurable via `CLIP_MAX_BODY_BYTES`).

For more details, see the **Security notes** section in `docs/USER.md`.
