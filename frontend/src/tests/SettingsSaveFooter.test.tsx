/**
 * SettingsSaveFooter.test.tsx — unit tests for the unified Save footer (F16).
 *
 * Covers:
 *   - Footer absent when isDirty = false
 *   - Footer present when isDirty = true
 *   - data-testid attributes: settings-save-footer, settings-save-btn, settings-discard-btn
 *   - Clicking discard calls discardDraft
 *   - Clicking save calls commitDraft (and i18n.changeLanguage)
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { SettingsSaveFooter } from "../components/settings/SettingsSaveFooter";

// ─── Mock loadLocale (FE-BUNDLE-1) ───────────────────────────────────────────
// loadLocale does a dynamic import + i18n.addResourceBundle which requires a fully
// initialized i18next instance. In unit tests we just want it to be a no-op.

vi.mock("../i18n/loadLocale", () => ({
  loadLocale: vi.fn().mockResolvedValue(undefined),
}));

// ─── Mock i18n ─────────────────────────────────────────────────────────────────

const mockChangeLanguage = vi.fn();

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const parts = key.split(".");
      return parts[parts.length - 1] ?? key;
    },
    i18n: { changeLanguage: mockChangeLanguage },
  }),
}));

// ─── Mock settingsStore ────────────────────────────────────────────────────────
// NOTE: variables whose names start with "mock" are hoisted by Vitest so they can
// be referenced inside vi.mock() factories (which are also hoisted).

const mockCommitDraft = vi.fn();
const mockDiscardDraft = vi.fn();

// Mutable state object: tests mutate properties (never reassign the const) before render.
const mockState = {
  draftTheme: "system",
  theme: "system",
  draftLanguage: "en",
  language: "en",
  draftConversationHistoryLength: 10,
  conversationHistoryLength: 10,
  draftContextWindowTokens: 32768,
  contextWindowTokens: 32768,
  commitDraft: mockCommitDraft,
  discardDraft: mockDiscardDraft,
};

vi.mock("../store/settingsStore", () => ({
  useSettingsStore: (selector: (s: typeof mockState) => unknown) => selector(mockState),
  selectIsDirty: (s: typeof mockState) =>
    s.draftTheme !== s.theme ||
    s.draftLanguage !== s.language ||
    s.draftConversationHistoryLength !== s.conversationHistoryLength ||
    s.draftContextWindowTokens !== s.contextWindowTokens,
  selectDraftLanguage: (s: typeof mockState) => s.draftLanguage,
  selectCommitDraft: (s: typeof mockState) => s.commitDraft,
  selectDiscardDraft: (s: typeof mockState) => s.discardDraft,
}));

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  // Reset to clean (non-dirty) state
  mockState.draftTheme = "system";
  mockState.theme = "system";
  mockState.draftLanguage = "en";
  mockState.language = "en";
  mockState.draftConversationHistoryLength = 10;
  mockState.conversationHistoryLength = 10;
  mockState.draftContextWindowTokens = 32768;
  mockState.contextWindowTokens = 32768;
  vi.clearAllMocks();
});

// ─── Tests ─────────────────────────────────────────────────────────────────────

describe("SettingsSaveFooter — hidden when not dirty", () => {
  it("renders nothing (null) when isDirty is false", () => {
    const { container } = render(<SettingsSaveFooter />);
    expect(container.firstChild).toBeNull();
    expect(document.querySelector('[data-testid="settings-save-footer"]')).toBeNull();
  });
});

describe("SettingsSaveFooter — visible when dirty", () => {
  beforeEach(() => {
    // Make state dirty via a draft/committed mismatch
    mockState.draftTheme = "dark";
  });

  it("renders the footer bar when isDirty is true", () => {
    render(<SettingsSaveFooter />);
    expect(screen.getByTestId("settings-save-footer")).toBeTruthy();
  });

  it("renders the Save button with data-testid=settings-save-btn", () => {
    render(<SettingsSaveFooter />);
    expect(screen.getByTestId("settings-save-btn")).toBeTruthy();
  });

  it("renders the Discard button with data-testid=settings-discard-btn", () => {
    render(<SettingsSaveFooter />);
    expect(screen.getByTestId("settings-discard-btn")).toBeTruthy();
  });

  it("Save button label is the 'save' i18n key (last segment)", () => {
    render(<SettingsSaveFooter />);
    // i18n mock returns last key segment: "settings.footer.save" → "save"
    expect(screen.getByTestId("settings-save-btn").textContent).toBe("save");
  });

  it("Discard button label is the 'discard' i18n key (last segment)", () => {
    render(<SettingsSaveFooter />);
    // i18n mock returns last key segment: "settings.footer.discard" → "discard"
    expect(screen.getByTestId("settings-discard-btn").textContent).toBe("discard");
  });
});

describe("SettingsSaveFooter — discard action", () => {
  beforeEach(() => {
    mockState.draftLanguage = "it"; // dirty
  });

  it("clicking Discard calls discardDraft once", () => {
    render(<SettingsSaveFooter />);
    fireEvent.click(screen.getByTestId("settings-discard-btn"));
    expect(mockDiscardDraft).toHaveBeenCalledTimes(1);
  });

  it("clicking Discard does NOT call commitDraft", () => {
    render(<SettingsSaveFooter />);
    fireEvent.click(screen.getByTestId("settings-discard-btn"));
    expect(mockCommitDraft).not.toHaveBeenCalled();
  });
});

describe("SettingsSaveFooter — save action", () => {
  beforeEach(() => {
    mockState.draftContextWindowTokens = 65536; // dirty
  });

  it("clicking Save calls commitDraft once", () => {
    render(<SettingsSaveFooter />);
    fireEvent.click(screen.getByTestId("settings-save-btn"));
    expect(mockCommitDraft).toHaveBeenCalledTimes(1);
  });

  it("clicking Save does NOT call discardDraft", () => {
    render(<SettingsSaveFooter />);
    fireEvent.click(screen.getByTestId("settings-save-btn"));
    expect(mockDiscardDraft).not.toHaveBeenCalled();
  });

  it("clicking Save calls i18n.changeLanguage with the draft language", async () => {
    // FE-BUNDLE-1: handleSave now calls loadLocale(lang) before changeLanguage,
    // so the changeLanguage call is asynchronous. Wrap in act() to flush microtasks.
    mockState.draftLanguage = "it";
    render(<SettingsSaveFooter />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("settings-save-btn"));
    });
    expect(mockChangeLanguage).toHaveBeenCalledWith("it");
  });
});

describe("SettingsSaveFooter — dirty on different fields", () => {
  it("footer is visible when only draftLanguage differs", () => {
    mockState.draftLanguage = "it";
    render(<SettingsSaveFooter />);
    expect(screen.getByTestId("settings-save-footer")).toBeTruthy();
  });

  it("footer is visible when only draftConversationHistoryLength differs", () => {
    mockState.draftConversationHistoryLength = 20;
    render(<SettingsSaveFooter />);
    expect(screen.getByTestId("settings-save-footer")).toBeTruthy();
  });

  it("footer is visible when only draftContextWindowTokens differs", () => {
    mockState.draftContextWindowTokens = 131072;
    render(<SettingsSaveFooter />);
    expect(screen.getByTestId("settings-save-footer")).toBeTruthy();
  });
});
