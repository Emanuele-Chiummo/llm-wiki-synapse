/**
 * ConnectScreen.test.tsx — unit tests for the Tauri first-launch gate (ADR-0047 §2.3).
 *
 * Covers:
 *   - Invalid scheme shows error and does NOT persist (ADR-0047 §2.7.1)
 *   - Failed /status probe shows error and does NOT persist (ADR-0047 §2.7.2 / §6 Do-NOT #4)
 *   - Successful /status probe (2xx) calls storeSetServerUrl and transitions
 *
 * PROJECT GOTCHA: vi.clearAllMocks() in beforeEach wipes mock implementations.
 * Re-set mock implementations inside each beforeEach.
 * jsdom is the test env; localStorage works natively.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { ConnectScreen } from "../components/connect/ConnectScreen";

// ─── Fake localStorage (Node.js 26 compat) ────────────────────────────────────

function makeFakeStorage(): Storage {
  let store: Record<string, string> = {};
  return {
    get length() { return Object.keys(store).length; },
    key(n: number) { return Object.keys(store)[n] ?? null; },
    getItem(k: string) { return Object.prototype.hasOwnProperty.call(store, k) ? (store[k] ?? null) : null; },
    setItem(k: string, v: string) { store[k] = v; },
    removeItem(k: string) { delete store[k]; },
    clear() { store = {}; },
  };
}

const fakeStorage = makeFakeStorage();
vi.stubGlobal("localStorage", fakeStorage);

// ─── Mocks ────────────────────────────────────────────────────────────────────

// Mock react-i18next: t(key) returns the last segment of the key
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, _opts?: Record<string, unknown>) => {
      const parts = key.split(".");
      return parts[parts.length - 1] ?? key;
    },
  }),
}));

// Mock the SVG asset (Vite handles SVGs as URLs; jsdom can't import them)
vi.mock("../../assets/synapse-logo.svg", () => ({ default: "/synapse-logo.svg" }));

// Mock @tauri-apps/plugin-http so that Tauri-mode tests (those that set
// __TAURI_INTERNALS__) work in jsdom.  platformFetch() dynamically imports this
// module when isTauri() is true; in tests the Tauri IPC is not available, so the
// real module would throw.  This mock delegates to globalThis.fetch (which is
// replaced per-test via vi.stubGlobal("fetch", mockFn)), keeping all existing
// fetch-call assertions unchanged — they still fire on the same mock function.
vi.mock("@tauri-apps/plugin-http", () => ({
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  fetch: (input: any, init?: any) => (globalThis as any).fetch(input, init) as unknown,
}));

// Capture the storeSetServerUrl mock so we can assert on it
const mockStoreSetServerUrl = vi.fn();

vi.mock("../store/settingsStore", () => ({
  useSettingsStore: (selector: (s: unknown) => unknown) =>
    selector({
      setServerUrl: mockStoreSetServerUrl,
    }),
  selectSetServerUrl: (s: { setServerUrl: unknown }) => s.setServerUrl,
  // SynapseMark (brand logo) reads the resolved theme — provide stubs so it renders
  selectTheme: () => "light",
  resolveTheme: () => "light",
}));

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  fakeStorage.clear();
  vi.clearAllMocks();
  // Re-set mock implementation after clearAllMocks (PROJECT GOTCHA)
  mockStoreSetServerUrl.mockImplementation(() => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
  fakeStorage.clear();
});

// ─── Helpers ──────────────────────────────────────────────────────────────────

function renderConnectScreen() {
  return render(<ConnectScreen />);
}

function getUrlInput() {
  return screen.getByRole("textbox") as HTMLInputElement;
}

function getConnectButton() {
  return screen.getByRole("button", { name: /connect/i });
}

function typeUrl(input: HTMLInputElement, url: string) {
  fireEvent.change(input, { target: { value: url } });
}

async function submitForm() {
  const btn = getConnectButton();
  await act(async () => {
    fireEvent.click(btn);
  });
}

// ─── Rendering ────────────────────────────────────────────────────────────────

describe("ConnectScreen — renders", () => {
  it("renders the connect screen container", () => {
    renderConnectScreen();
    expect(screen.getByTestId("connect-screen")).toBeTruthy();
  });

  it("renders a text input for the URL", () => {
    renderConnectScreen();
    expect(getUrlInput()).toBeTruthy();
  });

  it("renders a connect button", () => {
    renderConnectScreen();
    expect(getConnectButton()).toBeTruthy();
  });

  it("does not show an error on initial render", () => {
    renderConnectScreen();
    expect(screen.queryByTestId("connect-error")).toBeNull();
  });
});

// ─── Scheme validation (ADR-0047 §2.7.1) ─────────────────────────────────────

describe("ConnectScreen — invalid scheme shows error and does NOT persist", () => {
  it("shows error for javascript: scheme", async () => {
    vi.stubGlobal("fetch", vi.fn());
    renderConnectScreen();
    typeUrl(getUrlInput(), "javascript:alert(1)");
    await submitForm();
    expect(screen.getByTestId("connect-error")).toBeTruthy();
    expect(mockStoreSetServerUrl).not.toHaveBeenCalled();
  });

  it("shows error for file: scheme", async () => {
    vi.stubGlobal("fetch", vi.fn());
    renderConnectScreen();
    typeUrl(getUrlInput(), "file:///etc/passwd");
    await submitForm();
    expect(screen.getByTestId("connect-error")).toBeTruthy();
    expect(mockStoreSetServerUrl).not.toHaveBeenCalled();
  });

  it("shows error for tauri: scheme", async () => {
    vi.stubGlobal("fetch", vi.fn());
    renderConnectScreen();
    typeUrl(getUrlInput(), "tauri://localhost");
    await submitForm();
    expect(screen.getByTestId("connect-error")).toBeTruthy();
    expect(mockStoreSetServerUrl).not.toHaveBeenCalled();
  });

  it("fetch is NOT called when scheme is invalid", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderConnectScreen();
    typeUrl(getUrlInput(), "ftp://some-server");
    await submitForm();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

// ─── Failed probe (ADR-0047 §2.7.2 / §6 Do-NOT #4) ──────────────────────────

describe("ConnectScreen — failed /status probe shows error, does NOT persist", () => {
  it("shows error when fetch throws (network error / unreachable)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("Network error")),
    );
    renderConnectScreen();
    typeUrl(getUrlInput(), "http://unreachable-host:8000");
    await submitForm();
    await waitFor(() => {
      expect(screen.getByTestId("connect-error")).toBeTruthy();
    });
    expect(mockStoreSetServerUrl).not.toHaveBeenCalled();
  });

  it("shows error when /status returns non-2xx (e.g. 500)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 500 })),
    );
    renderConnectScreen();
    typeUrl(getUrlInput(), "http://myserver:8000");
    await submitForm();
    await waitFor(() => {
      expect(screen.getByTestId("connect-error")).toBeTruthy();
    });
    expect(mockStoreSetServerUrl).not.toHaveBeenCalled();
  });

  it("shows error when /status returns 404", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 404 })),
    );
    renderConnectScreen();
    typeUrl(getUrlInput(), "http://myserver:8000");
    await submitForm();
    await waitFor(() => {
      expect(screen.getByTestId("connect-error")).toBeTruthy();
    });
    expect(mockStoreSetServerUrl).not.toHaveBeenCalled();
  });

  it("shows error when fetch is aborted (simulated timeout)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("Request timed out", "AbortError")),
    );
    renderConnectScreen();
    typeUrl(getUrlInput(), "http://myserver:8000");
    await submitForm();
    await waitFor(() => {
      expect(screen.getByTestId("connect-error")).toBeTruthy();
    });
    expect(mockStoreSetServerUrl).not.toHaveBeenCalled();
  });

  it("does NOT persist the URL when probe fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("Network error")),
    );
    renderConnectScreen();
    typeUrl(getUrlInput(), "http://failing-server:8000");
    await submitForm();
    await waitFor(() => {
      expect(screen.getByTestId("connect-error")).toBeTruthy();
    });
    // localStorage must remain empty (base.ts setServerUrl was NOT called)
    expect(fakeStorage.getItem("synapse.serverUrl")).toBeNull();
    expect(mockStoreSetServerUrl).not.toHaveBeenCalled();
  });
});

// ─── Successful probe (ADR-0047 §2.7.2) ──────────────────────────────────────

describe("ConnectScreen — successful /status probe persists URL", () => {
  it("calls storeSetServerUrl with the validated URL on 2xx", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ vault_id: "default" }), { status: 200 }),
      ),
    );
    renderConnectScreen();
    typeUrl(getUrlInput(), "http://truenas:8000");
    await submitForm();
    await waitFor(() => {
      expect(mockStoreSetServerUrl).toHaveBeenCalledWith("http://truenas:8000");
    });
  });

  it("calls storeSetServerUrl with the trailing-slash stripped URL", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 200 })),
    );
    renderConnectScreen();
    typeUrl(getUrlInput(), "http://truenas:8000/");
    await submitForm();
    await waitFor(() => {
      expect(mockStoreSetServerUrl).toHaveBeenCalledWith("http://truenas:8000");
    });
  });

  it("probes the correct /status endpoint URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    renderConnectScreen();
    typeUrl(getUrlInput(), "http://mybackend:9000");
    await submitForm();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "http://mybackend:9000/status",
        expect.objectContaining({ signal: expect.anything() as AbortSignal }),
      );
    });
  });

  it("does NOT show an error on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 200 })),
    );
    renderConnectScreen();
    typeUrl(getUrlInput(), "http://truenas:8000");
    await submitForm();
    await waitFor(() => {
      expect(mockStoreSetServerUrl).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("connect-error")).toBeNull();
  });

  it("also accepts 201 as a success response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 201 })),
    );
    renderConnectScreen();
    typeUrl(getUrlInput(), "http://truenas:8000");
    await submitForm();
    await waitFor(() => {
      expect(mockStoreSetServerUrl).toHaveBeenCalledWith("http://truenas:8000");
    });
  });
});

// ─── Prefill + local auto-detect (first-launch UX) ────────────────────────────

describe("ConnectScreen — prefill and local auto-detect", () => {
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>)["__TAURI_INTERNALS__"];
  });

  it("prefills the input with the last connected server URL", () => {
    fakeStorage.setItem("synapse.lastServerUrl", "http://truenas:8000");
    vi.stubGlobal("fetch", vi.fn());
    renderConnectScreen();
    expect(getUrlInput().value).toBe("http://truenas:8000");
  });

  it("does NOT probe localhost on mount outside Tauri", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderConnectScreen();
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("probes localhost:8000 on mount in Tauri and prefills on 2xx", async () => {
    (window as unknown as Record<string, unknown>)["__TAURI_INTERNALS__"] = {};
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    renderConnectScreen();
    await waitFor(() => {
      expect(getUrlInput().value).toBe("http://localhost:8000");
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/status",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(screen.getByTestId("connect-detected")).toBeTruthy();
  });

  it("does NOT auto-probe in Tauri when a last server URL exists", async () => {
    (window as unknown as Record<string, unknown>)["__TAURI_INTERNALS__"] = {};
    fakeStorage.setItem("synapse.lastServerUrl", "http://truenas:8000");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderConnectScreen();
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(getUrlInput().value).toBe("http://truenas:8000");
  });

  it("keeps the empty prefill when the local probe fails", async () => {
    (window as unknown as Record<string, unknown>)["__TAURI_INTERNALS__"] = {};
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network"));
    vi.stubGlobal("fetch", fetchMock);
    renderConnectScreen();
    await act(async () => {
      await Promise.resolve();
    });
    expect(getUrlInput().value).toBe("http://");
    expect(screen.queryByTestId("connect-detected")).toBeNull();
  });

  // UXA-23: detected hint must contain a CheckCircle2 SVG icon
  it("UXA-23: detected hint contains an SVG icon alongside the text", async () => {
    (window as unknown as Record<string, unknown>)["__TAURI_INTERNALS__"] = {};
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 200 })),
    );
    renderConnectScreen();
    await waitFor(() => {
      expect(screen.getByTestId("connect-detected")).toBeTruthy();
    });
    const detectedEl = screen.getByTestId("connect-detected");
    // The paragraph must contain at least one SVG (CheckCircle2 icon, UXA-23)
    const svgIcons = detectedEl.querySelectorAll("svg");
    expect(svgIcons.length).toBeGreaterThanOrEqual(1);
  });
});
