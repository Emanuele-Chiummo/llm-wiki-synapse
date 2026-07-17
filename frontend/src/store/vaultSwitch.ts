/**
 * vaultSwitch.ts — FE-UIUX-3: single source of truth for the "soft" vault-switch sequence.
 *
 * Switching the active vault used to be a hard `window.location.reload()` (ProjectLauncher /
 * NewProjectWizard, ADR-0082 §2c: "the frontend reloads its stores on epoch change"). That lost
 * all UI state (panel sizes, chat scroll position, open dialogs) and was slow. This module
 * replaces the reload with an explicit reset of every vault-scoped Zustand store, so no
 * component ever observes a mix of old-vault data and new-vault vaultId (the frontend
 * equivalent of the cross-vault leak already fixed server-side in 1.9.1).
 *
 * Ordering matters: every vault-scoped store is reset to its initial (empty) state FIRST,
 * and appStore.vaultId is adopted LAST. Every section component's own `useEffect([vaultId])`
 * (GraphViewer, ReviewQueueView, LintView, IngestView, ChatSection/ConversationList, ...)
 * fires only once vaultId has actually changed, so ordering it last guarantees those effects
 * always see already-cleared stores as their "before" state — never a stale previous-vault
 * snapshot that a refetch races against.
 *
 * settingsStore / uiStore are intentionally NOT touched here: they hold GLOBAL client
 * preferences (theme, language, panel-open/closed, server URL) that are not vault-scoped.
 */

import { useAppStore } from "./appStore";
import { useGraphStore } from "./graphStore";
import { useIngestStore } from "./ingestStore";
import { useActivityStore } from "./activityStore";
import { useChatStore } from "./chatStore";
import { useLintStore } from "./lintStore";
import { useReviewStore } from "./reviewStore";
import { useResearchStore } from "./researchStore";
import { useImportScheduleStore } from "./importScheduleStore";
import { useProviderStore } from "./providerStore";
import { useStatusStore, refreshStatusNow } from "./statusStore";

/**
 * Reset every vault-scoped store for a switch to `newVaultId`, then perform an
 * immediate one-shot /status refresh (review-pending badge, dataVersion, vision
 * gate) so ActivityBar/NavRail don't wait for the next poll tick.
 *
 * Does NOT reload the page and does NOT itself fetch pages/graph — those are
 * owned by whichever section next mounts (SectionRouter mounts exactly one
 * section at a time) and its own vaultId-keyed effect.
 */
export function resetAllVaultStores(newVaultId: string): void {
  // Vault-scoped data/selection stores — cleared BEFORE the vaultId flip.
  useChatStore.getState().resetForVault();
  useGraphStore.getState().resetForVault();
  useIngestStore.getState().resetForVault();
  useActivityStore.getState().resetForVault();
  useLintStore.getState().resetForVault();
  useReviewStore.getState().resetForVault();
  useResearchStore.getState().resetForVault();
  useImportScheduleStore.getState().resetForVault();
  useProviderStore.getState().resetForVault();
  useStatusStore.getState().resetForVault();

  // Adopt the new vault id LAST — this is what every view's vaultId-effect depends on.
  useAppStore.getState().resetForVault(newVaultId);

  // One-shot immediate /status refresh (review badge, dataVersion, vision gate) —
  // does not wait for the next ActivityBar poll tick.
  refreshStatusNow();
}
