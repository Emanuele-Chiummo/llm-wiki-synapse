/**
 * Synapse Web Clipper — popup.js (Chrome MV3)
 *
 * Flow:
 * 1. On popup open, load options (baseURL, token, CF Access credentials) from
 *    chrome.storage.sync.
 * 2. Inject Readability + Turndown into the active tab via scripting.executeScript.
 * 3. Extract the article; show the title (editable by the user).
 * 4. On "Clip" click: POST JSON {url, title, markdown} to {baseURL}/clip with
 *    Authorization: Bearer {token} and Origin: chrome-extension://<id>.
 *    If both cfClientId and cfClientSecret are set, also sends:
 *      CF-Access-Client-Id: <cfClientId>
 *      CF-Access-Client-Secret: <cfClientSecret>
 *    These headers are required when the Synapse backend is behind Cloudflare Access.
 *    They are omitted entirely when unset (local/Tailscale access still works).
 * 5. Show success/error to the user.
 *
 * Security:
 * - Token and CF Access credentials read from chrome.storage.sync (encrypted
 *   at rest by Chrome; never logged or displayed in full after save).
 * - Origin header is automatically set by Chrome on cross-origin fetches from
 *   extensions (chrome-extension://<extension_id>).
 * - The server validates the token AND the Origin allowlist before writing.
 */

"use strict";

/* ── DOM refs ─────────────────────────────────────────────────────────────── */
const vaultSelect = document.getElementById("vaultSelect");
const titleInput = document.getElementById("titleInput");
const clipBtn = document.getElementById("clipBtn");
const statusEl = document.getElementById("status");

/* ── State ───────────────────────────────────────────────────────────────── */
let _markdown = "";
let _url = "";
let _settings = { baseURL: "", token: "", cfClientId: "", cfClientSecret: "" };
/** @type {string|null} Active vault_id from GET /projects; null if unknown. */
let _activeVaultId = null;

/* ── Helpers ─────────────────────────────────────────────────────────────── */
function setStatus(msg, cls = "inf") {
  statusEl.textContent = msg;
  statusEl.className = cls;
}

function setError(msg) {
  setStatus(msg, "err");
  clipBtn.disabled = true;
}

function setReady(msg) {
  setStatus(msg, "ok");
  clipBtn.disabled = false;
}

/* ── Vault picker ────────────────────────────────────────────────────────── */

/**
 * Fetch GET /projects and populate the vault <select>.
 * Non-active vaults are shown but disabled (the backend only accepts the active
 * vault; the user must switch active vault in Synapse before clipping there).
 * Falls back gracefully: if the endpoint fails, hides the picker row entirely.
 *
 * @param {string} baseURL  — Synapse base URL (already trimmed, no trailing slash)
 * @param {Record<string, string>} headers — auth + CF Access headers
 */
async function loadVaultPicker(baseURL, headers) {
  try {
    const resp = await fetch(`${baseURL}/projects`, { method: "GET", headers });
    if (!resp.ok) {
      // /projects may require auth; swallow silently and hide the picker
      vaultSelect.closest(".field").style.display = "none";
      return;
    }
    /** @type {{ projects: Array<{id: string, name: string}>, active_id: string|null }} */
    const data = await resp.json();
    _activeVaultId = data.active_id ?? null;

    // Clear loading placeholder
    vaultSelect.innerHTML = "";

    if (!data.projects || data.projects.length === 0) {
      vaultSelect.closest(".field").style.display = "none";
      return;
    }

    // Single-vault: hide the picker (no choice to make)
    if (data.projects.length === 1) {
      _activeVaultId = data.projects[0].id;
      vaultSelect.closest(".field").style.display = "none";
      return;
    }

    // Multi-vault: populate dropdown
    data.projects.forEach((project) => {
      const opt = document.createElement("option");
      opt.value = project.id;
      const isActive = project.id === data.active_id;
      opt.textContent = isActive
        ? `${project.name} [active]`
        : project.name;
      // Non-active vaults are visible but disabled: the server rejects cross-vault
      // clips (you must activate the target vault first via /projects/{id}/activate).
      opt.disabled = !isActive;
      if (isActive) {
        opt.selected = true;
      }
      vaultSelect.appendChild(opt);
    });

    vaultSelect.disabled = false;
  } catch (_err) {
    // Network error loading vaults — hide the picker, fall back to active vault
    vaultSelect.closest(".field").style.display = "none";
  }
}

/* ── Load settings ───────────────────────────────────────────────────────── */
async function loadSettings() {
  return new Promise((resolve) => {
    chrome.storage.sync.get(
      ["synapseBaseURL", "synapseToken", "cfClientId", "cfClientSecret"],
      (items) => {
        resolve({
          baseURL: (items.synapseBaseURL || "").replace(/\/$/, ""),
          token: items.synapseToken || "",
          // Cloudflare Access service-token credentials (both must be present
          // to pass the CF Access edge; omit both for local/Tailscale use).
          cfClientId: items.cfClientId || "",
          cfClientSecret: items.cfClientSecret || "",
        });
      }
    );
  });
}

/* ── Extract article via scripting.executeScript ─────────────────────────── */
async function extractArticle(tabId) {
  /**
   * This function runs INSIDE the page context (injected by scripting.executeScript).
   * It must be self-contained — no closures over popup-scope variables.
   * Returns {title, markdown, url} or throws.
   */
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    files: ["vendor/Readability.js", "vendor/turndown.js"],
  });

  // Inject the extraction logic
  const extractionResults = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => {
      try {
        // Clone the document so Readability can mutate it
        const docClone = document.cloneNode(true);
        const reader = new Readability(docClone, { keepClasses: false });
        const article = reader.parse();

        if (!article || !article.content) {
          return { error: "Readability could not extract article content." };
        }

        // Convert HTML → Markdown
        const td = new TurndownService({
          headingStyle: "atx",
          codeBlockStyle: "fenced",
          bulletListMarker: "-",
        });

        // Enable GFM tables plugin if available
        if (typeof turndownPluginGfm !== "undefined") {
          td.use(turndownPluginGfm.gfm);
        } else {
          // Minimal table support: turn <table> into a code block if no plugin
          td.addRule("table", {
            filter: "table",
            replacement(content, node) {
              return "\n\n" + node.outerHTML + "\n\n";
            },
          });
        }

        const markdown = td.turndown(article.content);

        return {
          title: article.title || document.title || "",
          markdown,
          url: window.location.href,
        };
      } catch (e) {
        return { error: String(e) };
      }
    },
  });

  if (!extractionResults || extractionResults.length === 0) {
    throw new Error("Script injection returned no results.");
  }

  const result = extractionResults[0].result;
  if (!result) {
    throw new Error("No result from content extraction.");
  }
  if (result.error) {
    throw new Error(result.error);
  }

  return result;
}

/* ── Clip action ─────────────────────────────────────────────────────────── */
async function doClip() {
  clipBtn.disabled = true;
  setStatus("Sending to Synapse...", "inf");

  const title = titleInput.value.trim();
  // Include the selected vault_id (W5 / PF-MCP-VAULT-1 vault picker).
  // The server validates that vault_id matches the active vault; if the user
  // somehow selects a non-active vault (e.g. picker wasn't populated correctly),
  // the backend returns 400 with a clear message. When _activeVaultId is null,
  // omit the field entirely so the server falls back to its active vault.
  const selectedVaultId = vaultSelect.value || _activeVaultId || null;
  const body = {
    url: _url,
    title,
    markdown: _markdown,
    ...(selectedVaultId ? { vault_id: selectedVaultId } : {}),
  };

  // ── Build request headers ──────────────────────────────────────────────
  // CF-Access-Client-Id / CF-Access-Client-Secret are only added when BOTH
  // values are configured in Settings.  Omitting them leaves the standard
  // bearer-token path intact for local/Tailscale deployments.
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${_settings.token}`,
    // Chrome sets Origin automatically for cross-origin extension fetches.
    // When posting to localhost (same-site from extension perspective) it may
    // be omitted — the server's allow-without-origin path covers this.
  };
  if (_settings.cfClientId && _settings.cfClientSecret) {
    headers["CF-Access-Client-Id"] = _settings.cfClientId;
    headers["CF-Access-Client-Secret"] = _settings.cfClientSecret;
  }

  try {
    const resp = await fetch(`${_settings.baseURL}/clip`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });

    if (resp.ok) {
      const data = await resp.json();
      const verb = data.overwritten ? "Updated" : "Saved";
      setStatus(`${verb}: ${data.file_path}`, "ok");
    } else {
      let detail = `HTTP ${resp.status}`;
      try {
        const err = await resp.json();
        detail += ` — ${err.detail || JSON.stringify(err)}`;
      } catch (_) {
        // non-JSON error body
      }
      setStatus(detail, "err");
      clipBtn.disabled = false;
    }
  } catch (err) {
    setStatus(`Network error: ${err.message}`, "err");
    clipBtn.disabled = false;
  }
}

/* ── Initialise ──────────────────────────────────────────────────────────── */
(async () => {
  _settings = await loadSettings();

  if (!_settings.baseURL) {
    setError("No Synapse URL set. Open Settings to configure.");
    return;
  }
  if (!_settings.token) {
    setError("No clip token set. Open Settings to configure.");
    return;
  }

  // Get the active tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) {
    setError("Cannot access the active tab.");
    return;
  }

  // Check that we can inject scripts (not a chrome:// or PDF page)
  if (!tab.url || tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://")) {
    setError("Cannot clip this type of page.");
    return;
  }

  // Build auth headers (same as doClip) so loadVaultPicker can call /projects
  const authHeadersForProjects = {
    Authorization: `Bearer ${_settings.token}`,
  };
  if (_settings.cfClientId && _settings.cfClientSecret) {
    authHeadersForProjects["CF-Access-Client-Id"] = _settings.cfClientId;
    authHeadersForProjects["CF-Access-Client-Secret"] = _settings.cfClientSecret;
  }

  // Load vault picker (W5 — runs in parallel with article extraction)
  const vaultPickerPromise = loadVaultPicker(_settings.baseURL, authHeadersForProjects);

  setStatus("Extracting article...", "inf");

  try {
    const [, { title, markdown, url }] = await Promise.all([
      vaultPickerPromise,
      extractArticle(tab.id),
    ]);
    _markdown = markdown;
    _url = url || tab.url;
    titleInput.value = title;
    setReady(`Ready to clip — ${markdown.length} chars extracted`);
  } catch (err) {
    setError(`Extraction failed: ${err.message}`);
  }

  clipBtn.addEventListener("click", doClip);
})();
