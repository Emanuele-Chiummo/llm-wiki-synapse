/**
 * Header.test.tsx — unit tests for the Header component (ADR-0048 §T4a server dropdown).
 *
 * Covers:
 *   - Header renders in non-Tauri mode (no server chip)
 *   - Header renders server chip in Tauri mode
 *   - Server dropdown lists known servers when chip is clicked
 *   - Clicking a different server calls setServerUrl + window.location.reload
 *   - Clicking "change server" calls clearServerUrl
 *   - Dropdown closes on Escape
 *
 * @vitest-environment jsdom
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// ─── Fake localStorage ────────────────────────────────────────────────────────

function makeFakeStorage(): Storage {
  let store: Record<string, string> = {};
  return {
    get length() { return Object.keys(store).length; },
    key(n: number) { return Object.keys(store)[n] ?? null; },
    getItem(k: string) {
      return Object.prototype.hasOwnProperty.call(store, k) ? (store[k] ?? null) : null;
    },
    setItem(k: string, v: string) { store[k] = v; },
    removeItem(k: string) { delete store[k]; },
    clear() { store = {}; },
  };
}

const fakeStorage = makeFakeStorage();
vi.stubGlobal("localStorage", fakeStorage);

// ─── Mock react-i18next ───────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, _opts?: Record<string, unknown>) => {
      // Return the last segment of the key for easy assertions
      const parts = key.split(".");
      return parts[parts.length - 1] ?? key;
    },
  }),
}));

// ─── Mock SVG asset ───────────────────────────────────────────────────────────

vi.mock("../../assets/synapse-logo.svg", () => ({ default: "/synapse-logo.svg" }));

// ─── Mock ProviderSelector ────────────────────────────────────────────────────

vi.mock("../components/provider/ProviderSelector", () => ({
  ProviderSelector: () => <div data-testid="provider-selector" />,
}));

// ─── Mock useDesktopZoom (no-op in tests) ─────────────────────────────────────

vi.mock("../hooks/useDesktopZoom", () => ({
  useDesktopZoom: () => undefined,
}));

// ─── State tracking for settingsStore mock ────────────────────────────────────

let mockServerUrl: string | null = "http://truenas:8000";
const mockClearServerUrl = vi.fn();
const mockSetServerUrl = vi.fn();

vi.mock("../store/settingsStore", () => ({
  useSettingsStore: (selector: (s: unknown) => unknown) =>
    selector({
      serverUrl: mockServerUrl,
      clearServerUrl: mockClearServerUrl,
      setServerUrl: mockSetServerUrl,
    }),
  selectServerUrl: (s: { serverUrl: string | null }) => s.serverUrl,
  selectClearServerUrl: (s: { clearServerUrl: unknown }) => s.clearServerUrl,
  selectSetServerUrl: (s: { setServerUrl: unknown }) => s.setServerUrl,
}));

// ─── api/base mock — controlled isTauri and getKnownServers ──────────────────

let mockIsTauri = false;
let mockKnownServers: string[] = [];

vi.mock("../api/base", () => ({
  isTauri: () => mockIsTauri,
  getKnownServers: () => mockKnownServers,
  apiBase: () => "",
  getServerUrl: () => null,
  setServerUrl: vi.fn(),
  clearServerUrl: vi.fn(),
}));

// ─── Import after mocks ───────────────────────────────────────────────────────

import { Header } from "../components/Header";

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  fakeStorage.clear();
  mockIsTauri = false;
  mockKnownServers = [];
  mockServerUrl = "http://truenas:8000";
  vi.clearAllMocks();
  mockClearServerUrl.mockImplementation(() => undefined);
  mockSetServerUrl.mockImplementation(() => undefined);
  // Stub window.location.reload to avoid JSDOM navigation error
  vi.stubGlobal("location", { ...window.location, reload: vi.fn() });
  // Reset __TAURI_INTERNALS__
  const w = globalThis as Record<string, unknown>;
  if ("__TAURI_INTERNALS__" in w) delete w["__TAURI_INTERNALS__"];
});

afterEach(() => {
  vi.restoreAllMocks();
  fakeStorage.clear();
  const w = globalThis as Record<string, unknown>;
  if ("__TAURI_INTERNALS__" in w) delete w["__TAURI_INTERNALS__"];
});

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("Header — non-Tauri mode", () => {
  it("renders without crashing", () => {
    render(<Header />);
    expect(screen.getByTestId("app-header")).toBeTruthy();
  });

  it("does NOT render the server chip in non-Tauri mode", () => {
    mockIsTauri = false;
    render(<Header />);
    expect(screen.queryByTestId("server-chip")).toBeNull();
  });

  it("renders the ProviderSelector", () => {
    render(<Header />);
    expect(screen.getByTestId("provider-selector")).toBeTruthy();
  });

  // R11-3: verify Header still owns the primary branding after NavRail logo removal
  it("R11-3: renders the Synapse logo <img> in the brand slot", () => {
    render(<Header />);
    const brand = document.querySelector(".app-header__brand");
    expect(brand, "app-header__brand div must be present").not.toBeNull();
    const img = brand!.querySelector("img");
    expect(img, "Header brand slot must contain a logo <img>").not.toBeNull();
    expect(img!.getAttribute("src")).toBeTruthy();
  });

  it("R11-3: renders the 'Synapse' wordmark text in the Header", () => {
    render(<Header />);
    const brand = document.querySelector(".app-header__brand");
    expect(brand?.textContent).toContain("Synapse");
  });
});

describe("Header — Tauri mode with server chip", () => {
  beforeEach(() => {
    mockIsTauri = true;
    mockServerUrl = "http://truenas:8000";
    mockKnownServers = ["http://truenas:8000"];
  });

  it("renders the server chip button when in Tauri mode with a connected server", () => {
    render(<Header />);
    expect(screen.getByTestId("server-chip-btn")).toBeTruthy();
  });

  it("shows the server host in the chip", () => {
    render(<Header />);
    expect(screen.getByTestId("server-chip-btn").textContent).toContain("truenas:8000");
  });

  it("does NOT render the dropdown menu initially (closed by default)", () => {
    render(<Header />);
    expect(screen.queryByTestId("server-chip-menu")).toBeNull();
  });

  it("opens the dropdown when the chip button is clicked", () => {
    render(<Header />);
    fireEvent.click(screen.getByTestId("server-chip-btn"));
    expect(screen.getByTestId("server-chip-menu")).toBeTruthy();
  });

  it("lists known servers in the dropdown", () => {
    mockKnownServers = ["http://truenas:8000", "http://server-b:9000"];
    render(<Header />);
    fireEvent.click(screen.getByTestId("server-chip-btn"));
    const menu = screen.getByTestId("server-chip-menu");
    expect(menu.textContent).toContain("truenas:8000");
    expect(menu.textContent).toContain("server-b:9000");
  });

  it("marks the current server in the dropdown", () => {
    mockKnownServers = ["http://truenas:8000", "http://server-b:9000"];
    render(<Header />);
    fireEvent.click(screen.getByTestId("server-chip-btn"));
    // The "current" label is from t("desktop.serverChip.current") → "current"
    expect(screen.getByTestId("server-chip-menu").textContent).toContain("current");
  });

  it("closes the dropdown on Escape key", async () => {
    render(<Header />);
    fireEvent.click(screen.getByTestId("server-chip-btn"));
    expect(screen.getByTestId("server-chip-menu")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByTestId("server-chip-menu")).toBeNull();
    });
  });
});

describe("Header — server switching (ADR-0048 §T4a)", () => {
  beforeEach(() => {
    mockIsTauri = true;
    mockServerUrl = "http://truenas:8000";
    mockKnownServers = ["http://truenas:8000", "http://server-b:9000"];
  });

  it("clicking a different server calls setServerUrl", () => {
    render(<Header />);
    fireEvent.click(screen.getByTestId("server-chip-btn"));
    const serverBItem = screen.getByTestId("server-item-server-b:9000");
    fireEvent.click(serverBItem);
    expect(mockSetServerUrl).toHaveBeenCalledWith("http://server-b:9000");
  });

  it("clicking a different server calls window.location.reload", () => {
    render(<Header />);
    fireEvent.click(screen.getByTestId("server-chip-btn"));
    fireEvent.click(screen.getByTestId("server-item-server-b:9000"));
    expect(window.location.reload).toHaveBeenCalled();
  });

  it("clicking the current server does NOT call setServerUrl or reload", () => {
    render(<Header />);
    fireEvent.click(screen.getByTestId("server-chip-btn"));
    // The current server is truenas:8000
    fireEvent.click(screen.getByTestId("server-item-truenas:8000"));
    expect(mockSetServerUrl).not.toHaveBeenCalled();
    expect(window.location.reload).not.toHaveBeenCalled();
  });

  it("clicking 'change server' calls clearServerUrl", () => {
    render(<Header />);
    fireEvent.click(screen.getByTestId("server-chip-btn"));
    fireEvent.click(screen.getByTestId("change-server-btn"));
    expect(mockClearServerUrl).toHaveBeenCalled();
  });

  it("dropdown closes after clicking 'change server'", async () => {
    render(<Header />);
    fireEvent.click(screen.getByTestId("server-chip-btn"));
    expect(screen.getByTestId("server-chip-menu")).toBeTruthy();
    fireEvent.click(screen.getByTestId("change-server-btn"));
    await waitFor(() => {
      expect(screen.queryByTestId("server-chip-menu")).toBeNull();
    });
  });
});

describe("Header — no server URL in Tauri mode", () => {
  it("does NOT render server chip when serverUrl is null", () => {
    mockIsTauri = true;
    mockServerUrl = null;
    render(<Header />);
    expect(screen.queryByTestId("server-chip")).toBeNull();
  });
});
