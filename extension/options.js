/**
 * Synapse Web Clipper — options.js
 *
 * Manages persistent settings in chrome.storage.sync:
 *   synapseBaseURL  — the FastAPI service URL
 *   synapseToken    — the CLIP_TOKEN bearer value (stored encrypted by Chrome)
 *   cfClientId      — Cloudflare Access service-token Client ID (optional)
 *   cfClientSecret  — Cloudflare Access service-token Client Secret (optional,
 *                     stored encrypted by Chrome, never shown in full after save)
 *
 * CF Access headers are only injected into backend requests when BOTH cfClientId
 * and cfClientSecret are non-empty.  Leave both blank for local/Tailscale access.
 *
 * Security notes:
 * - The token and CF Secret are stored in chrome.storage.sync which Chrome
 *   encrypts at rest.
 * - Secrets are never displayed in full after save (password inputs stay masked).
 * - The extension ID is read-only — the user must copy it to CLIP_ALLOWED_ORIGINS.
 */

"use strict";

const baseURLInput = document.getElementById("baseURL");
const clipTokenInput = document.getElementById("clipToken");
const extensionIdInput = document.getElementById("extensionId");
const cfClientIdInput = document.getElementById("cfClientId");
const cfClientSecretInput = document.getElementById("cfClientSecret");
const saveBtn = document.getElementById("saveBtn");
const testBtn = document.getElementById("testBtn");
const statusMsg = document.getElementById("statusMsg");

function setStatus(msg, cls = "inf") {
  statusMsg.textContent = msg;
  statusMsg.className = cls;
}

/* ── Show extension ID ───────────────────────────────────────────────────── */
extensionIdInput.value = chrome.runtime.id;

/* ── Load saved settings ─────────────────────────────────────────────────── */
chrome.storage.sync.get(
  ["synapseBaseURL", "synapseToken", "cfClientId", "cfClientSecret"],
  (items) => {
    if (items.synapseBaseURL) {
      baseURLInput.value = items.synapseBaseURL;
    }
    if (items.synapseToken) {
      // Show a masked placeholder so the user knows a token is set
      clipTokenInput.placeholder = "Token saved (enter new value to replace)";
    }
    // CF Client ID is not secret — show it directly in the field
    if (items.cfClientId) {
      cfClientIdInput.value = items.cfClientId;
    }
    // CF Client Secret is sensitive — use the same masked-placeholder pattern
    if (items.cfClientSecret) {
      cfClientSecretInput.placeholder = "Secret saved (enter new value to replace)";
    }
  }
);

/* ── Save settings ───────────────────────────────────────────────────────── */
saveBtn.addEventListener("click", () => {
  const baseURL = baseURLInput.value.trim().replace(/\/$/, "");
  const token = clipTokenInput.value.trim();
  const cfClientId = cfClientIdInput.value.trim();
  const cfClientSecret = cfClientSecretInput.value.trim();

  if (!baseURL) {
    setStatus("Please enter the Synapse base URL.", "err");
    return;
  }

  const toSave = { synapseBaseURL: baseURL };
  // Only update each secret if the user typed something new
  if (token) toSave.synapseToken = token;
  if (cfClientId) toSave.cfClientId = cfClientId;
  if (cfClientSecret) toSave.cfClientSecret = cfClientSecret;

  // If the user cleared the Client ID field, remove both CF Access keys so
  // the popup stops sending CF headers (useful when switching to Tailscale).
  const toRemove = cfClientId === "" ? ["cfClientId", "cfClientSecret"] : [];

  const doSet = () => {
    chrome.storage.sync.set(toSave, () => {
      if (chrome.runtime.lastError) {
        setStatus(`Save failed: ${chrome.runtime.lastError.message}`, "err");
        return;
      }
      // Reset input fields to masked-placeholder state
      clipTokenInput.value = "";
      clipTokenInput.placeholder = "Token saved (enter new value to replace)";
      if (cfClientSecret) {
        cfClientSecretInput.value = "";
        cfClientSecretInput.placeholder = "Secret saved (enter new value to replace)";
      }
      if (toRemove.length > 0) {
        cfClientIdInput.value = "";
        cfClientSecretInput.value = "";
        cfClientSecretInput.placeholder = "Paste client secret here";
      }
      setStatus("Settings saved.", "ok");
    });
  };

  if (toRemove.length > 0) {
    // Clear CF Access keys first, then persist remaining settings
    chrome.storage.sync.remove(toRemove, () => {
      if (chrome.runtime.lastError) {
        setStatus(`Save failed: ${chrome.runtime.lastError.message}`, "err");
        return;
      }
      doSet();
    });
  } else {
    doSet();
  }
});

/* ── Test connection ─────────────────────────────────────────────────────── */
testBtn.addEventListener("click", async () => {
  setStatus("Testing connection...", "inf");

  const baseURL = baseURLInput.value.trim().replace(/\/$/, "");
  if (!baseURL) {
    setStatus("Enter a base URL first.", "err");
    return;
  }

  // Retrieve saved credentials (live input values take precedence, so an
  // unsaved change in the fields is tested without requiring a Save first).
  const stored = await new Promise((resolve) => {
    chrome.storage.sync.get(
      ["synapseToken", "cfClientId", "cfClientSecret"],
      (items) => resolve(items)
    );
  });

  const token = clipTokenInput.value.trim() || stored.synapseToken || "";
  const cfClientId =
    cfClientIdInput.value.trim() || stored.cfClientId || "";
  const cfClientSecret =
    cfClientSecretInput.value.trim() || stored.cfClientSecret || "";

  // Build headers: add CF Access pair only when both values are present
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  if (cfClientId && cfClientSecret) {
    headers["CF-Access-Client-Id"] = cfClientId;
    headers["CF-Access-Client-Secret"] = cfClientSecret;
  }

  try {
    const resp = await fetch(`${baseURL}/status`, {
      method: "GET",
      headers,
    });

    if (resp.ok) {
      const data = await resp.json();
      setStatus(
        `Connected — vault_id=${data.vault_id}, data_version=${data.data_version}`,
        "ok",
      );
    } else {
      setStatus(`Server returned HTTP ${resp.status}`, "err");
    }
  } catch (err) {
    setStatus(`Connection failed: ${err.message}`, "err");
  }
});
