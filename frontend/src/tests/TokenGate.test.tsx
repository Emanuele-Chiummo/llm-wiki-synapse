/**
 * TokenGate.test.tsx — unit tests for the web 401 overlay (ADR-0052 §4.5).
 *
 * Covers:
 *   - Renders on authRequired (data-testid="token-gate")
 *   - Token field is password type with Eye/EyeOff toggle
 *   - Shows error when token is empty on submit
 *   - Shows error on 401 from protected probe
 *   - Calls onSuccess after successful probe
 *
 * @vitest-environment jsdom
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { TokenGate } from "../components/connect/TokenGate";

// ─── Fake localStorage ────────────────────────────────────────────────────────

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

// Mock react-i18next
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const parts = key.split(".");
      return parts[parts.length - 1] ?? key;
    },
  }),
}));

// Mock synapse-logo
vi.mock("../../assets/synapse-logo.svg", () => ({ default: "/synapse-logo.svg" }));

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  fakeStorage.clear();
  vi.clearAllMocks();
});

afterEach(() => {
  fakeStorage.clear();
  vi.restoreAllMocks();
});

// ─── Rendering ────────────────────────────────────────────────────────────────

describe("TokenGate — renders", () => {
  it("renders the token gate container", () => {
    vi.stubGlobal("fetch", vi.fn());
    render(<TokenGate onSuccess={vi.fn()} />);
    expect(screen.getByTestId("token-gate")).toBeTruthy();
  });

  it("renders a password-type input", () => {
    vi.stubGlobal("fetch", vi.fn());
    render(<TokenGate onSuccess={vi.fn()} />);
    // password inputs don't have ARIA role "textbox" — query by type directly
    const input = document.querySelector("input[type=password]") as HTMLInputElement | null;
    expect(input).toBeTruthy();
  });
});

// ─── Token field show/hide toggle ─────────────────────────────────────────────

describe("TokenGate — Eye/EyeOff toggle", () => {
  it("token field starts as password type", () => {
    vi.stubGlobal("fetch", vi.fn());
    render(<TokenGate onSuccess={vi.fn()} />);
    const input = document.querySelector("input") as HTMLInputElement;
    expect(input.type).toBe("password");
  });

  it("clicking show-token button changes type to text", async () => {
    vi.stubGlobal("fetch", vi.fn());
    render(<TokenGate onSuccess={vi.fn()} />);
    const toggleBtn = screen.getByRole("button", { name: /show/i });
    await act(async () => { fireEvent.click(toggleBtn); });
    const input = document.querySelector("input") as HTMLInputElement;
    expect(input.type).toBe("text");
  });

  it("clicking again hides the token (password type)", async () => {
    vi.stubGlobal("fetch", vi.fn());
    render(<TokenGate onSuccess={vi.fn()} />);
    const toggleBtn = screen.getByRole("button", { name: /show/i });
    await act(async () => { fireEvent.click(toggleBtn); });
    const toggleBtn2 = screen.getByRole("button", { name: /hide/i });
    await act(async () => { fireEvent.click(toggleBtn2); });
    const input = document.querySelector("input") as HTMLInputElement;
    expect(input.type).toBe("password");
  });
});

// ─── Submit validation ────────────────────────────────────────────────────────

describe("TokenGate — empty token shows error", () => {
  it("shows error when submitted with empty token", async () => {
    vi.stubGlobal("fetch", vi.fn());
    const onSuccess = vi.fn();
    render(<TokenGate onSuccess={onSuccess} />);
    // Find the Authenticate button
    const submitBtn = document.querySelector("button[type=submit]") as HTMLButtonElement;
    // Token is empty — button is disabled; force-enable for test
    submitBtn.removeAttribute("disabled");
    await act(async () => { fireEvent.click(submitBtn); });
    expect(screen.getByTestId("token-gate-error")).toBeTruthy();
    expect(onSuccess).not.toHaveBeenCalled();
  });
});

// ─── 401 from protected probe ─────────────────────────────────────────────────

describe("TokenGate — 401 from protected probe shows error", () => {
  it("shows error and does not call onSuccess on 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 401 })));
    const onSuccess = vi.fn();
    render(<TokenGate onSuccess={onSuccess} />);
    const input = document.querySelector("input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "wrong-token" } });
    const submitBtn = document.querySelector("button[type=submit]") as HTMLButtonElement;
    await act(async () => { fireEvent.click(submitBtn); });
    await waitFor(() => {
      expect(screen.getByTestId("token-gate-error")).toBeTruthy();
    });
    expect(onSuccess).not.toHaveBeenCalled();
  });
});

// ─── Successful probe ─────────────────────────────────────────────────────────

describe("TokenGate — successful probe calls onSuccess", () => {
  it("calls onSuccess after 200 from protected probe", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
    const onSuccess = vi.fn();
    render(<TokenGate onSuccess={onSuccess} />);
    const input = document.querySelector("input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "correct-token" } });
    const submitBtn = document.querySelector("button[type=submit]") as HTMLButtonElement;
    await act(async () => { fireEvent.click(submitBtn); });
    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalledTimes(1);
    });
  });
});
