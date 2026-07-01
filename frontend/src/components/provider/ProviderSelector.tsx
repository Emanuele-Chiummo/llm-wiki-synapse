/**
 * ProviderSelector.tsx — Header slot dropdown for F17 (ADR-0018 §4).
 *
 * Layout:
 *   - Collapsed: shows active provider label + mode chip + ▾ chevron.
 *   - Expanded: floating panel with:
 *       - Scope toggle (Vault / Global)
 *       - Sorted list of providers: name / mode chip / model_id / capability label
 *       - One-click select → POST /provider/config with current scope
 *
 * INVARIANT I6: no hardcoded provider_type or model_id literals. All values from
 *               GET /provider/config. Capability labels via i18n keys only.
 * INVARIANT I3: subscribes to providerStore only via typed selectors.
 *               No cross-store reads here.
 *
 * Capability derivation (client-side, no new API call):
 *   provider_type === "cli"   → delegated
 *   model supports tools      → orchestratedTools  (heuristic: model_id contains "claude")
 *   else                      → orchestrated
 * This is informational only — the actual routing decision is server-side.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";
import {
  useProviderStore,
  selectProviderList,
  selectActiveProvider,
  selectProviderLoading,
  selectProviderError,
  selectWriteScope,
  selectFetchProviderList,
  selectSetActiveProvider,
  selectSetWriteScope,
} from "../../store/providerStore";
import { useGraphStore, selectVaultId } from "../../store/graphStore";
import { showToast } from "../common/Toast";
import type { ProviderConfigItem } from "../../api/types";

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Derive a capability label key for a provider row (ADR-0018 §4).
 * Informational only — routing is server-side.
 */
function capabilityKey(item: ProviderConfigItem): string {
  if (item.provider_type === "cli") return "provider.capability.delegated";
  // Heuristic: if model_id looks like a Claude model or is null (inherits),
  // assume tool-use support → orchestratedTools
  const mid = (item.model_id ?? "").toLowerCase();
  if (mid.includes("claude") || mid.includes("gpt") || mid === "") {
    return "provider.capability.orchestratedTools";
  }
  return "provider.capability.orchestrated";
}

/** Short display label for a provider row (I6 — use t() for type). */
function providerLabel(item: ProviderConfigItem, t: (k: string) => string): string {
  const typeName = t(`provider.type.${item.provider_type}` as string) || item.provider_type;
  if (item.model_id) return `${typeName} / ${item.model_id}`;
  return typeName;
}

// ─── Component ───────────────────────────────────────────────────────────────

export function ProviderSelector() {
  const { t } = useTranslation();
  const list = useProviderStore(useShallow(selectProviderList));
  const active = useProviderStore(selectActiveProvider);
  const loading = useProviderStore(selectProviderLoading);
  const error = useProviderStore(selectProviderError);
  const writeScope = useProviderStore(selectWriteScope);
  const fetchList = useProviderStore(selectFetchProviderList);
  const setActive = useProviderStore(selectSetActiveProvider);
  const setWriteScope = useProviderStore(selectSetWriteScope);
  const vaultId = useGraphStore(selectVaultId);

  const [open, setOpen] = useState(false);
  const [writing, setWriting] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  // Fetch on first open
  useEffect(() => {
    if (open && list.length === 0) {
      void fetchList();
    }
  }, [open, list.length, fetchList]);

  // Derive active on fetch
  useEffect(() => {
    if (list.length > 0) {
      useProviderStore.getState().deriveActive(vaultId);
    }
  }, [list, vaultId]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (
        panelRef.current && !panelRef.current.contains(e.target as Node) &&
        triggerRef.current && !triggerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const handleSelect = useCallback(
    async (item: ProviderConfigItem) => {
      setWriting(true);
      try {
        await setActive(item.provider_type, item.model_id, item.base_url, writeScope, vaultId);
        showToast(t("provider.changed"), "success");
        setOpen(false);
      } catch {
        showToast(t("provider.changeError"), "error");
      } finally {
        setWriting(false);
      }
    },
    [setActive, writeScope, vaultId, t],
  );

  // ── Trigger label ─────────────────────────────────────────────────────────

  const triggerLabel = active
    ? providerLabel(active, t)
    : loading
    ? t("common.loading")
    : t("provider.label");

  return (
    <div style={{ position: "relative" }}>
      {/* Trigger button */}
      <button
        ref={triggerRef}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${t("provider.label")}: ${triggerLabel}`}
        data-testid="provider-selector-trigger"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "4px 10px",
          border: "1px solid var(--syn-border)",
          borderRadius: 6,
          background: open ? "var(--syn-surface-hover)" : "transparent",
          color: "var(--syn-text)",
          fontSize: 12,
          cursor: "pointer",
          whiteSpace: "nowrap",
          maxWidth: 220,
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        <span
          aria-hidden="true"
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: error ? "var(--syn-red)" : active ? "var(--syn-green)" : "var(--syn-text-dim)",
            flexShrink: 0,
          }}
        />
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", flex: 1, textAlign: "left" }}>
          {triggerLabel}
        </span>
        <span aria-hidden="true" style={{ fontSize: 10, opacity: 0.6, flexShrink: 0 }}>▾</span>
      </button>

      {/* Dropdown panel */}
      {open && (
        <div
          ref={panelRef}
          role="dialog"
          aria-label={t("provider.label")}
          data-testid="provider-selector-panel"
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            right: 0,
            zIndex: 1000,
            width: 320,
            background: "var(--syn-surface)",
            border: "1px solid var(--syn-border)",
            borderRadius: 8,
            boxShadow: "var(--syn-shadow-pop)",
            overflow: "hidden",
          }}
        >
          {/* Scope toggle */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "10px 14px",
              borderBottom: "1px solid var(--syn-border)",
            }}
          >
            <span style={{ fontSize: 11, color: "var(--syn-text-muted)", flex: 1 }}>{t("provider.scope.vault")} / {t("provider.scope.global")}</span>
            <div
              role="group"
              aria-label="Scope"
              style={{ display: "flex", gap: 4 }}
            >
              {(["vault", "global"] as const).map((scope) => (
                <button
                  key={scope}
                  onClick={() => setWriteScope(scope)}
                  aria-pressed={writeScope === scope}
                  style={{
                    padding: "3px 8px",
                    border: "1px solid var(--syn-border)",
                    borderRadius: 4,
                    background: writeScope === scope ? "var(--syn-accent-soft)" : "transparent",
                    color: writeScope === scope ? "var(--syn-accent)" : "var(--syn-text-muted)",
                    fontSize: 11,
                    cursor: "pointer",
                    fontWeight: writeScope === scope ? 600 : 400,
                  }}
                >
                  {t(`provider.scope.${scope}`)}
                </button>
              ))}
            </div>
          </div>

          {/* Provider list */}
          <div
            role="listbox"
            aria-label={t("provider.label")}
            style={{ maxHeight: 280, overflow: "auto" }}
          >
            {loading && (
              <div style={{ padding: "12px 14px", fontSize: 12, color: "var(--syn-text-dim)" }}>
                {t("common.loading")}
              </div>
            )}
            {!loading && list.length === 0 && (
              <div style={{ padding: "12px 14px", fontSize: 12, color: "var(--syn-text-dim)" }}>
                {t("provider.noProviders")}
              </div>
            )}
            {list.map((item) => {
              const isActive = active?.id === item.id;
              const capability = capabilityKey(item);
              const label = providerLabel(item, t);
              return (
                <button
                  key={item.id}
                  role="option"
                  aria-selected={isActive}
                  onClick={() => void handleSelect(item)}
                  disabled={writing}
                  data-testid="provider-option"
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 10,
                    width: "100%",
                    padding: "8px 14px",
                    border: "none",
                    borderBottom: "1px solid var(--syn-border-subtle)",
                    background: isActive ? "var(--syn-accent-soft)" : "transparent",
                    color: "var(--syn-text)",
                    textAlign: "left",
                    cursor: writing ? "wait" : "pointer",
                    fontSize: 12,
                  }}
                >
                  {/* Active indicator */}
                  <span
                    aria-hidden="true"
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      background: isActive ? "var(--syn-green)" : "var(--syn-border)",
                      flexShrink: 0,
                      marginTop: 4,
                    }}
                  />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {label}
                    </div>
                    <div style={{ fontSize: 10, color: "var(--syn-text-muted)", marginTop: 2, display: "flex", gap: 6, flexWrap: "wrap" }}>
                      <span
                        style={{
                          padding: "1px 5px",
                          borderRadius: 3,
                          background: "var(--syn-surface-hover)",
                          color: "var(--syn-text-muted)",
                        }}
                      >
                        {t(`provider.scope.${item.scope}`)}
                      </span>
                      <span
                        style={{
                          padding: "1px 5px",
                          borderRadius: 3,
                          background: "var(--syn-accent-soft)",
                          color: "var(--syn-accent)",
                        }}
                      >
                        {t(capability)}
                      </span>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>

          {/* Error */}
          {error && (
            <div
              role="alert"
              style={{
                padding: "8px 14px",
                borderTop: "1px solid var(--syn-border)",
                fontSize: 11,
                color: "var(--syn-red)",
              }}
            >
              {error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
