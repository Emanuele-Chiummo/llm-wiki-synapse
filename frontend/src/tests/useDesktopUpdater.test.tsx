/**
 * useDesktopUpdater.test.tsx — unit tests for the startup update check hook
 * and UpdateBanner component (ADR-0049 §U4).
 *
 * Test matrix:
 *   1. Hook is a no-op when not in Tauri (isTauri() = false).
 *   2. Hook exposes { version, notes } when check() resolves with an update.
 *   3. Hook stays idle (null) when check() resolves with null (already up to date).
 *   4. Hook swallows errors from check() (fire-and-forget invariant).
 *   5. Banner renders with version text and both action buttons when update is present.
 *   6. "Later" button calls dismiss() and banner disappears.
 *   7. "Update now" triggers mocked downloadAndInstall + relaunch.
 *   8. Error path shows error text and keeps the banner visible.
 *
 * PROJECT GOTCHAS:
 *   - vi.clearAllMocks() wipes implementations; re-set in beforeEach.
 *   - Stub __TAURI_INTERNALS__ on window per-test, delete in afterEach.
 *   - Tauri plugin modules mocked via vi.mock() at the top level.
 *
 * @vitest-environment jsdom
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, render, screen, fireEvent, waitFor } from "@testing-library/react";

// ─── Mock Tauri plugin modules ────────────────────────────────────────────────
// Must be at the top level so vitest hoists them.

const mockCheck = vi.fn();
const mockDownloadAndInstall = vi.fn();
const mockRelaunch = vi.fn();

vi.mock("@tauri-apps/plugin-updater", () => ({
  check: () => mockCheck(),
}));

vi.mock("@tauri-apps/plugin-process", () => ({
  relaunch: () => mockRelaunch(),
}));

// ─── Mock api/base — isTauri() controlled per test ───────────────────────────

let tauriEnabled = false;

vi.mock("../api/base", () => ({
  isTauri: () => tauriEnabled,
  apiBase: () => "",
  getServerUrl: () => null,
  setServerUrl: vi.fn(),
  clearServerUrl: vi.fn(),
}));

// ─── Mock react-i18next ───────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      // Simulate basic {{version}} interpolation for the available key
      const lastSeg = key.split(".").pop() ?? key;
      if (opts && "version" in opts) {
        return `${lastSeg}:${String(opts["version"])}`;
      }
      return lastSeg;
    },
    i18n: { changeLanguage: vi.fn() },
  }),
}));

// ─── Imports under test (after vi.mock hoisting) ──────────────────────────────

import { useDesktopUpdater } from "../hooks/useDesktopUpdater";
import { UpdateBanner } from "../components/common/UpdateBanner";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function enableTauri() {
  tauriEnabled = true;
  (window as unknown as Record<string, unknown>)["__TAURI_INTERNALS__"] = {};
}

function disableTauri() {
  tauriEnabled = false;
  delete (window as unknown as Record<string, unknown>)["__TAURI_INTERNALS__"];
}

function makeUpdateObject(version = "0.7.0", body?: string) {
  return {
    version,
    body,
    downloadAndInstall: mockDownloadAndInstall,
  };
}

// ─── Setup / teardown ─────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  disableTauri();

  // Re-set implementations after clearAllMocks (PROJECT GOTCHA).
  mockCheck.mockResolvedValue(null);
  mockDownloadAndInstall.mockResolvedValue(undefined);
  mockRelaunch.mockResolvedValue(undefined);
});

afterEach(() => {
  disableTauri();
  vi.restoreAllMocks();
});

// ─── 1. Hook no-op when not in Tauri ─────────────────────────────────────────

describe("useDesktopUpdater — no-op outside Tauri", () => {
  it("returns null update when isTauri() is false", async () => {
    disableTauri();
    // check() must NOT be called in this path
    const { result } = renderHook(() => useDesktopUpdater());

    // Give microtasks a chance to flush
    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.update).toBeNull();
    // The plugin check() should never be called when not in Tauri
    expect(mockCheck).not.toHaveBeenCalled();
  });

  it("installing is false initially outside Tauri", () => {
    disableTauri();
    const { result } = renderHook(() => useDesktopUpdater());
    expect(result.current.installing).toBe(false);
  });

  it("installError is null initially outside Tauri", () => {
    disableTauri();
    const { result } = renderHook(() => useDesktopUpdater());
    expect(result.current.installError).toBeNull();
  });
});

// ─── 2. Hook exposes update info when check() returns an update ───────────────

describe("useDesktopUpdater — update available", () => {
  it("sets update.version when check() returns an update", async () => {
    enableTauri();
    mockCheck.mockResolvedValue(makeUpdateObject("0.7.0", "Bug fixes"));

    const { result } = renderHook(() => useDesktopUpdater());

    await waitFor(() => {
      expect(result.current.update).not.toBeNull();
    });

    expect(result.current.update?.version).toBe("0.7.0");
  });

  it("sets update.notes from the manifest body", async () => {
    enableTauri();
    mockCheck.mockResolvedValue(makeUpdateObject("0.7.0", "Performance improvements"));

    const { result } = renderHook(() => useDesktopUpdater());

    await waitFor(() => {
      expect(result.current.update).not.toBeNull();
    });

    expect(result.current.update?.notes).toBe("Performance improvements");
  });

  it("sets update.notes to undefined when body is absent", async () => {
    enableTauri();
    mockCheck.mockResolvedValue(makeUpdateObject("0.7.0", undefined));

    const { result } = renderHook(() => useDesktopUpdater());

    await waitFor(() => {
      expect(result.current.update).not.toBeNull();
    });

    expect(result.current.update?.notes).toBeUndefined();
  });
});

// ─── 3. Hook stays idle when no update is available ──────────────────────────

describe("useDesktopUpdater — no update (already up to date)", () => {
  it("keeps update null when check() returns null", async () => {
    enableTauri();
    mockCheck.mockResolvedValue(null);

    const { result } = renderHook(() => useDesktopUpdater());

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.update).toBeNull();
  });
});

// ─── 4. Hook swallows errors from check() ────────────────────────────────────

describe("useDesktopUpdater — error swallowing", () => {
  it("does not throw when check() rejects", async () => {
    enableTauri();
    mockCheck.mockRejectedValue(new Error("Network timeout"));

    // Should NOT throw
    expect(() => renderHook(() => useDesktopUpdater())).not.toThrow();
  });

  it("keeps update null when check() rejects", async () => {
    enableTauri();
    mockCheck.mockRejectedValue(new Error("Network timeout"));

    const { result } = renderHook(() => useDesktopUpdater());

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.update).toBeNull();
  });

  it("keeps installError null when check() rejects (errors from startup check are swallowed)", async () => {
    enableTauri();
    mockCheck.mockRejectedValue(new TypeError("fetch failed"));

    const { result } = renderHook(() => useDesktopUpdater());

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.installError).toBeNull();
  });
});

// ─── 5. Banner renders with version and action buttons ───────────────────────

describe("UpdateBanner — renders when update present", () => {
  it("renders the banner when update is non-null", () => {
    const state = {
      update: { version: "0.7.0", notes: "Bug fixes" },
      installing: false,
      installError: null,
      dismiss: vi.fn(),
      startInstall: vi.fn(),
    };
    render(<UpdateBanner state={state} />);
    expect(screen.getByTestId("update-banner")).toBeTruthy();
  });

  it("shows the version in the available text", () => {
    const state = {
      update: { version: "0.7.0", notes: undefined },
      installing: false,
      installError: null,
      dismiss: vi.fn(),
      startInstall: vi.fn(),
    };
    render(<UpdateBanner state={state} />);
    // t("desktop.update.available", { version: "0.7.0" }) → "available:0.7.0" from our mock
    const el = screen.getByTestId("update-available-text");
    expect(el.textContent).toContain("0.7.0");
  });

  it("renders the 'Update now' button", () => {
    const state = {
      update: { version: "0.7.0", notes: undefined },
      installing: false,
      installError: null,
      dismiss: vi.fn(),
      startInstall: vi.fn(),
    };
    render(<UpdateBanner state={state} />);
    expect(screen.getByTestId("update-now-btn")).toBeTruthy();
  });

  it("renders the 'Later' button", () => {
    const state = {
      update: { version: "0.7.0", notes: undefined },
      installing: false,
      installError: null,
      dismiss: vi.fn(),
      startInstall: vi.fn(),
    };
    render(<UpdateBanner state={state} />);
    expect(screen.getByTestId("update-later-btn")).toBeTruthy();
  });

  it("does NOT render the banner when update is null", () => {
    const state = {
      update: null,
      installing: false,
      installError: null,
      dismiss: vi.fn(),
      startInstall: vi.fn(),
    };
    render(<UpdateBanner state={state} />);
    expect(screen.queryByTestId("update-banner")).toBeNull();
  });
});

// ─── 6. "Later" dismisses the banner (session-scoped) ────────────────────────

describe("UpdateBanner — Later button dismisses", () => {
  it("calls dismiss() when Later is clicked", () => {
    const mockDismiss = vi.fn();
    const state = {
      update: { version: "0.7.0", notes: undefined },
      installing: false,
      installError: null,
      dismiss: mockDismiss,
      startInstall: vi.fn(),
    };
    render(<UpdateBanner state={state} />);
    fireEvent.click(screen.getByTestId("update-later-btn"));
    expect(mockDismiss).toHaveBeenCalledTimes(1);
  });

  it("banner disappears after dismiss() is called (state collapses to null)", () => {
    // Simulate the real hook dismissal by rendering with null update after dismiss
    const { rerender } = render(
      <UpdateBanner
        state={{
          update: { version: "0.7.0", notes: undefined },
          installing: false,
          installError: null,
          dismiss: vi.fn(),
          startInstall: vi.fn(),
        }}
      />,
    );

    expect(screen.getByTestId("update-banner")).toBeTruthy();

    // After dismiss → update becomes null
    rerender(
      <UpdateBanner
        state={{
          update: null,
          installing: false,
          installError: null,
          dismiss: vi.fn(),
          startInstall: vi.fn(),
        }}
      />,
    );

    expect(screen.queryByTestId("update-banner")).toBeNull();
  });
});

// ─── 7. "Update now" triggers downloadAndInstall + relaunch ──────────────────

describe("UpdateBanner / useDesktopUpdater — Update now flow", () => {
  it("calls startInstall() when 'Update now' is clicked", () => {
    const mockStartInstall = vi.fn().mockResolvedValue(undefined);
    const state = {
      update: { version: "0.7.0", notes: undefined },
      installing: false,
      installError: null,
      dismiss: vi.fn(),
      startInstall: mockStartInstall,
    };
    render(<UpdateBanner state={state} />);
    fireEvent.click(screen.getByTestId("update-now-btn"));
    expect(mockStartInstall).toHaveBeenCalledTimes(1);
  });

  it("shows installing text when installing=true", () => {
    const state = {
      update: { version: "0.7.0", notes: undefined },
      installing: true,
      installError: null,
      dismiss: vi.fn(),
      startInstall: vi.fn(),
    };
    render(<UpdateBanner state={state} />);
    expect(screen.getByTestId("update-installing-text")).toBeTruthy();
  });

  it("hides action buttons during install", () => {
    const state = {
      update: { version: "0.7.0", notes: undefined },
      installing: true,
      installError: null,
      dismiss: vi.fn(),
      startInstall: vi.fn(),
    };
    render(<UpdateBanner state={state} />);
    expect(screen.queryByTestId("update-now-btn")).toBeNull();
    expect(screen.queryByTestId("update-later-btn")).toBeNull();
  });

  it("hook: downloadAndInstall is called via startInstall() when in Tauri", async () => {
    enableTauri();
    // First call (startup check) returns the update
    // Second call (startInstall re-checks) also returns the update
    const updateObj = makeUpdateObject("0.7.0", "Notes");
    mockCheck.mockResolvedValue(updateObj);
    mockDownloadAndInstall.mockResolvedValue(undefined);
    mockRelaunch.mockResolvedValue(undefined);

    const { result } = renderHook(() => useDesktopUpdater());

    // Wait for startup check to complete
    await waitFor(() => {
      expect(result.current.update).not.toBeNull();
    });

    // Trigger install
    await act(async () => {
      await result.current.startInstall();
    });

    expect(mockDownloadAndInstall).toHaveBeenCalledTimes(1);
  });

  it("hook: relaunch is called after successful downloadAndInstall", async () => {
    enableTauri();
    const updateObj = makeUpdateObject("0.7.0");
    mockCheck.mockResolvedValue(updateObj);
    mockDownloadAndInstall.mockResolvedValue(undefined);
    mockRelaunch.mockResolvedValue(undefined);

    const { result } = renderHook(() => useDesktopUpdater());

    await waitFor(() => {
      expect(result.current.update).not.toBeNull();
    });

    await act(async () => {
      await result.current.startInstall();
    });

    expect(mockRelaunch).toHaveBeenCalledTimes(1);
  });
});

// ─── 8. Error path shows error inline and keeps banner ───────────────────────

describe("UpdateBanner — error path", () => {
  it("shows error text when installError is non-null", () => {
    const state = {
      update: { version: "0.7.0", notes: undefined },
      installing: false,
      installError: "Download failed",
      dismiss: vi.fn(),
      startInstall: vi.fn(),
    };
    render(<UpdateBanner state={state} />);
    expect(screen.getByTestId("update-error-text")).toBeTruthy();
  });

  it("banner remains visible when there is an installError (retry possible)", () => {
    const state = {
      update: { version: "0.7.0", notes: undefined },
      installing: false,
      installError: "Download failed",
      dismiss: vi.fn(),
      startInstall: vi.fn(),
    };
    render(<UpdateBanner state={state} />);
    expect(screen.getByTestId("update-banner")).toBeTruthy();
  });

  it("still shows action buttons on error state (retry is possible via Update now)", () => {
    const state = {
      update: { version: "0.7.0", notes: undefined },
      installing: false,
      installError: "Download failed",
      dismiss: vi.fn(),
      startInstall: vi.fn(),
    };
    render(<UpdateBanner state={state} />);
    expect(screen.getByTestId("update-now-btn")).toBeTruthy();
    expect(screen.getByTestId("update-later-btn")).toBeTruthy();
  });

  it("hook: sets installError when downloadAndInstall rejects", async () => {
    enableTauri();
    const updateObj = makeUpdateObject("0.7.0");
    mockCheck.mockResolvedValue(updateObj);
    mockDownloadAndInstall.mockRejectedValue(new Error("Network error during download"));
    mockRelaunch.mockResolvedValue(undefined);

    const { result } = renderHook(() => useDesktopUpdater());

    await waitFor(() => {
      expect(result.current.update).not.toBeNull();
    });

    await act(async () => {
      await result.current.startInstall();
    });

    expect(result.current.installError).not.toBeNull();
    expect(result.current.installing).toBe(false);
    // relaunch should NOT be called if download failed
    expect(mockRelaunch).not.toHaveBeenCalled();
  });

  it("hook: installError contains the error message", async () => {
    enableTauri();
    const updateObj = makeUpdateObject("0.7.0");
    mockCheck.mockResolvedValue(updateObj);
    mockDownloadAndInstall.mockRejectedValue(new Error("Disk full"));
    mockRelaunch.mockResolvedValue(undefined);

    const { result } = renderHook(() => useDesktopUpdater());

    await waitFor(() => {
      expect(result.current.update).not.toBeNull();
    });

    await act(async () => {
      await result.current.startInstall();
    });

    expect(result.current.installError).toContain("Disk full");
  });
});
