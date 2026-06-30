/**
 * Synapse Web Clipper — options.js
 *
 * Manages persistent settings in chrome.storage.sync:
 *   synapseBaseURL — the FastAPI service URL
 *   synapseToken   — the CLIP_TOKEN bearer value (stored encrypted by Chrome)
 *
 * Security notes:
 * - The token is stored in chrome.storage.sync which Chrome encrypts at rest.
 * - The token is never displayed in full after save (password input stays masked).
 * - The extension ID is read-only — the user must copy it to CLIP_ALLOWED_ORIGINS.
 */

"use strict";

const baseURLInput = document.getElementById("baseURL");
const clipTokenInput = document.getElementById("clipToken");
const extensionIdInput = document.getElementById("extensionId");
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
chrome.storage.sync.get(["synapseBaseURL", "synapseToken"], (items) => {
  if (items.synapseBaseURL) {
    baseURLInput.value = items.synapseBaseURL;
  }
  if (items.synapseToken) {
    // Show a masked placeholder so the user knows a token is set
    clipTokenInput.placeholder = "Token saved (enter new value to replace)";
  }
});

/* ── Save settings ───────────────────────────────────────────────────────── */
saveBtn.addEventListener("click", () => {
  const baseURL = baseURLInput.value.trim().replace(/\/$/, "");
  const token = clipTokenInput.value.trim();

  if (!baseURL) {
    setStatus("Please enter the Synapse base URL.", "err");
    return;
  }

  const toSave = { synapseBaseURL: baseURL };
  // Only update the token if the user typed something new
  if (token) {
    toSave.synapseToken = token;
  }

  chrome.storage.sync.set(toSave, () => {
    if (chrome.runtime.lastError) {
      setStatus(`Save failed: ${chrome.runtime.lastError.message}`, "err");
    } else {
      clipTokenInput.value = "";
      clipTokenInput.placeholder = "Token saved (enter new value to replace)";
      setStatus("Settings saved.", "ok");
    }
  });
});

/* ── Test connection ─────────────────────────────────────────────────────── */
testBtn.addEventListener("click", async () => {
  setStatus("Testing connection...", "inf");

  const baseURL = baseURLInput.value.trim().replace(/\/$/, "");
  if (!baseURL) {
    setStatus("Enter a base URL first.", "err");
    return;
  }

  // Retrieve the current token (may be saved even if input is blank)
  const stored = await new Promise((resolve) => {
    chrome.storage.sync.get(["synapseToken"], (items) => resolve(items));
  });

  const token = clipTokenInput.value.trim() || stored.synapseToken || "";

  try {
    const resp = await fetch(`${baseURL}/status`, {
      method: "GET",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
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
