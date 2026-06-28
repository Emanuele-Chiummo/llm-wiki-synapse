/**
 * importScheduleStore.ts — Zustand store for scheduled folder import state (ADR-0020 §5).
 *
 * INVARIANT I3: separate from graphStore (and settingsStore) so schedule polling/changes
 *               never re-render the graph or the page tree.
 * INVARIANT I7: polling uses a bounded setTimeout chain with AbortController — stops when
 *               last_status is no longer "running". Never a setInterval leak.
 *
 * State lives here; UI reads via typed selectors + useShallow for objects.
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type {
  ImportSchedule,
  ImportSchedulePutBody,
  ImportSchedulePutResponse,
} from "../api/types";
import {
  getImportSchedule,
  putImportSchedule,
  runImportNow,
} from "../api/importScheduleClient";

// ─── Constants ────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 3_000; // poll every 3s while status === "running"

// ─── State / Actions ─────────────────────────────────────────────────────────

interface ImportScheduleState {
  schedule: ImportSchedule | null;
  loading: boolean;
  saving: boolean;
  running: boolean;    // run-now in flight
  error: string | null;
  saveError: string | null;
  /** dir_ok/dir_message from the last PUT response */
  dirOk: boolean | null;
  dirMessage: string | null;
}

interface ImportScheduleActions {
  fetchSchedule: (signal?: AbortSignal) => Promise<void>;
  saveSchedule: (body: ImportSchedulePutBody, signal?: AbortSignal) => Promise<ImportSchedulePutResponse | null>;
  runNow: (signal?: AbortSignal) => Promise<void>;
  /** Start polling while status === "running". Returns cleanup fn. */
  startPollingIfRunning: () => () => void;
  /** Clear transient errors/warnings. */
  clearErrors: () => void;
}

export type ImportScheduleStore = ImportScheduleState & ImportScheduleActions;

// ─── Store ────────────────────────────────────────────────────────────────────

export const useImportScheduleStore = create<ImportScheduleStore>((set, get) => ({
  schedule: null,
  loading: false,
  saving: false,
  running: false,
  error: null,
  saveError: null,
  dirOk: null,
  dirMessage: null,

  fetchSchedule: async (signal) => {
    set({ loading: true, error: null });
    try {
      const s = await getImportSchedule(signal);
      set({ schedule: s, loading: false });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      set({ error: (err as Error).message, loading: false });
    }
  },

  saveSchedule: async (body, signal) => {
    set({ saving: true, saveError: null, dirOk: null, dirMessage: null });
    try {
      const res = await putImportSchedule(body, signal);
      set({
        schedule: res,
        saving: false,
        dirOk: res.dir_ok,
        dirMessage: res.dir_message,
      });
      return res;
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return null;
      set({ saveError: (err as Error).message, saving: false });
      return null;
    }
  },

  runNow: async (signal) => {
    set({ running: true, error: null });
    try {
      await runImportNow(signal);
      set({ running: false });
      // Refresh schedule state after triggering a run
      void get().fetchSchedule();
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") { set({ running: false }); return; }
      set({ error: (err as Error).message, running: false });
    }
  },

  startPollingIfRunning: () => {
    const ctrl = new AbortController();

    async function tick() {
      if (ctrl.signal.aborted) return;
      const { schedule } = get();
      if (schedule?.last_status !== "running") return; // bounded: stop when done
      try {
        const s = await getImportSchedule(ctrl.signal);
        if (!ctrl.signal.aborted) {
          set({ schedule: s });
          if (s.last_status === "running") {
            setTimeout(() => void tick(), POLL_INTERVAL_MS);
          }
        }
      } catch {
        // stop polling on error
      }
    }

    setTimeout(() => void tick(), POLL_INTERVAL_MS);
    return () => ctrl.abort();
  },

  clearErrors: () => set({ error: null, saveError: null }),
}));

// ─── Typed selectors (I3) ─────────────────────────────────────────────────────

export function selectImportSchedule(s: ImportScheduleStore): ImportSchedule | null {
  return s.schedule;
}
export function selectImportLoading(s: ImportScheduleStore): boolean { return s.loading; }
export function selectImportSaving(s: ImportScheduleStore): boolean { return s.saving; }
export function selectImportRunning(s: ImportScheduleStore): boolean { return s.running; }
export function selectImportError(s: ImportScheduleStore): string | null { return s.error; }
export function selectImportSaveError(s: ImportScheduleStore): string | null { return s.saveError; }
export function selectDirOk(s: ImportScheduleStore): boolean | null { return s.dirOk; }
export function selectDirMessage(s: ImportScheduleStore): string | null { return s.dirMessage; }
export function selectFetchSchedule(s: ImportScheduleStore): ImportScheduleActions["fetchSchedule"] { return s.fetchSchedule; }
export function selectSaveSchedule(s: ImportScheduleStore): ImportScheduleActions["saveSchedule"] { return s.saveSchedule; }
export function selectRunNow(s: ImportScheduleStore): ImportScheduleActions["runNow"] { return s.runNow; }
export function selectStartPollingIfRunning(s: ImportScheduleStore): ImportScheduleActions["startPollingIfRunning"] { return s.startPollingIfRunning; }

/** Hook: schedule object — shallow equality (I3). */
export function useImportSchedule(): ImportSchedule | null {
  return useImportScheduleStore(useShallow(selectImportSchedule));
}
