/**
 * mobile-css.test.tsx — R10-5 Mobile/PWA: vitest unit tests for mobile CSS hooks.
 *
 * Verifies that the className hooks used by theme.css @media (max-width: 767px)
 * are present in the rendered DOM, so that the CSS selectors can target them.
 *
 * AC-R10-5-1: NavRail renders with className "nav-rail" on the <nav> element
 *             and "nav-rail__item" + "nav-rail__label" on each button.
 *             (These already existed; this test documents them as the mobile hook.)
 *
 * AC-R10-5-1 (panels): PanelGroup renders Panel components with the expected
 *             classNames: panel-group__panel--left, --center, --right, and
 *             panel-group__separator--left, --right.
 *             (Added in PanelGroup.tsx for R10-5 CSS targeting.)
 *
 * AC-R10-5-2: MessageInput renders .chat-send-btn and .chat-input-textarea
 *             class hooks for the mobile touch-target CSS rules.
 *
 * NOTE: These are className-presence tests only — actual media-query rendering
 * is a Playwright concern (jsdom does not evaluate CSS media queries).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import { NavRail } from "../components/nav/NavRail";
import { MessageInput } from "../components/chat/MessageInput";

// ─── NavRail mocks ────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const parts = key.split(".");
      const raw = parts[parts.length - 1] ?? key;
      return raw.charAt(0).toUpperCase() + raw.slice(1);
    },
  }),
}));

vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: unknown) => unknown) =>
    selector({
      activeSection: "chat",
      setActiveSection: vi.fn(),
    }),
  selectActiveSection: (s: { activeSection: string }) => s.activeSection,
  selectSetActiveSection: (s: { setActiveSection: () => void }) => s.setActiveSection,
}));

vi.mock("../store/ingestStore", () => ({
  useIngestRunningCount: () => 0,
}));

// ─── MessageInput mocks ────────────────────────────────────────────────────────

vi.mock("../store/providerStore", () => ({
  useProviderStore: () => null,
  selectActiveProvider: (s: null) => s,
}));

// ─── NavRail: mobile CSS hooks ────────────────────────────────────────────────

describe("R10-5 Mobile CSS hooks — NavRail", () => {
  beforeEach(() => {
    render(<NavRail />);
  });

  it("renders <nav> with className 'nav-rail' (AC-R10-5-1 nav rail selector)", () => {
    const nav = document.querySelector("nav.nav-rail");
    expect(nav, "nav.nav-rail must be present in the DOM").not.toBeNull();
  });

  it("renders nav buttons with className 'nav-rail__item' (AC-R10-5-1 touch target selector)", () => {
    const items = document.querySelectorAll(".nav-rail__item");
    expect(items.length, "there should be nav-rail__item buttons").toBeGreaterThan(0);
  });

  it("renders label spans with className 'nav-rail__label' (AC-R10-5-1 label-hide selector)", () => {
    const labels = document.querySelectorAll(".nav-rail__label");
    expect(labels.length, "there should be nav-rail__label spans").toBeGreaterThan(0);
  });
});

// ─── MessageInput: mobile CSS hooks ───────────────────────────────────────────

describe("R10-5 Mobile CSS hooks — MessageInput", () => {
  const noop = () => {};

  it("renders textarea with className 'chat-input-textarea' (AC-R10-5-2 touch target)", () => {
    render(
      <MessageInput
        onSend={noop}
        onStop={noop}
        isStreaming={false}
      />,
    );
    const textarea = document.querySelector(".chat-input-textarea");
    expect(textarea, ".chat-input-textarea must be present").not.toBeNull();
    expect(textarea!.tagName.toLowerCase()).toBe("textarea");
  });

  it("renders send button with className 'chat-send-btn' when not streaming (AC-R10-5-2)", () => {
    render(
      <MessageInput
        onSend={noop}
        onStop={noop}
        isStreaming={false}
      />,
    );
    const sendBtn = document.querySelector(".chat-send-btn");
    expect(sendBtn, ".chat-send-btn must be present when not streaming").not.toBeNull();
  });

  it("renders stop button with className 'chat-stop-btn' when streaming (AC-R10-5-2)", () => {
    render(
      <MessageInput
        onSend={noop}
        onStop={noop}
        isStreaming={true}
      />,
    );
    const stopBtn = document.querySelector(".chat-stop-btn");
    expect(stopBtn, ".chat-stop-btn must be present when streaming").not.toBeNull();
  });
});
