/**
 * Synapse Web Clipper — popup.js (Chrome MV3)
 *
 * Flow:
 * 1. On popup open, load options (baseURL, token) from chrome.storage.sync.
 * 2. Inject Readability + Turndown into the active tab via scripting.executeScript.
 * 3. Extract the article; show the title (editable by the user).
 * 4. On "Clip" click: POST JSON {url, title, markdown} to {baseURL}/clip with
 *    Authorization: Bearer {token} and Origin: chrome-extension://<id>.
 * 5. Show success/error to the user.
 *
 * Security:
 * - Token read from chrome.storage.sync (encrypted at rest by Chrome; never
 *   logged or displayed in full after save).
 * - Origin header is automatically set by Chrome on cross-origin fetches from
 *   extensions (chrome-extension://<extension_id>).
 * - The server validates the token AND the Origin allowlist before writing.
 */

"use strict";

/* ── DOM refs ─────────────────────────────────────────────────────────────── */
const titleInput = document.getElementById("titleInput");
const clipBtn = document.getElementById("clipBtn");
const statusEl = document.getElementById("status");

/* ── State ───────────────────────────────────────────────────────────────── */
let _markdown = "";
let _url = "";
let _settings = { baseURL: "", token: "" };

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

/* ── Load settings ───────────────────────────────────────────────────────── */
async function loadSettings() {
  return new Promise((resolve) => {
    chrome.storage.sync.get(["synapseBaseURL", "synapseToken"], (items) => {
      resolve({
        baseURL: (items.synapseBaseURL || "").replace(/\/$/, ""),
        token: items.synapseToken || "",
      });
    });
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
  const body = {
    url: _url,
    title,
    markdown: _markdown,
  };

  try {
    const resp = await fetch(`${_settings.baseURL}/clip`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${_settings.token}`,
        // Chrome sets Origin automatically for cross-origin extension fetches.
        // When posting to localhost (same-site from extension perspective) it may
        // be omitted — the server's allow-without-origin path covers this.
      },
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

  setStatus("Extracting article...", "inf");

  try {
    const { title, markdown, url } = await extractArticle(tab.id);
    _markdown = markdown;
    _url = url || tab.url;
    titleInput.value = title;
    setReady(`Ready to clip — ${markdown.length} chars extracted`);
  } catch (err) {
    setError(`Extraction failed: ${err.message}`);
  }

  clipBtn.addEventListener("click", doClip);
})();
