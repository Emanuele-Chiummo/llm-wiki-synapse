/**
 * PanelDrawer.tsx — overlay drawer component (ADR-0057 §4).
 *
 * Slides in from left or right over the full viewport as a portal to document.body.
 * Backdrop dims the rest of the UI; clicking it or pressing Esc closes the drawer.
 *
 * Focus trap (no new dependency):
 *   - On open:  focus the drawer container (tabIndex={-1}).
 *   - Tab key:  cycles within focusable elements inside the drawer.
 *   - On close: returns focus to the element that was focused before opening.
 *
 * CSS: transform-only transition for GPU compositing (no layout thrash).
 * prefers-reduced-motion: suppressed via theme.css (.panel-drawer class).
 *
 * INVARIANT I4: content (NavTree / PreviewPanel) is passed as children;
 * the virtualizer's scroll container stays mounted — no remount issues.
 */

import React, { useEffect, useRef, useCallback, type ReactNode } from "react";
import { createPortal } from "react-dom";

// ─── Focus-trap helpers ────────────────────────────────────────────────────────

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(", ");

function getFocusable(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.hasAttribute("aria-hidden"),
  );
}

// ─── Props ─────────────────────────────────────────────────────────────────────

export interface PanelDrawerProps {
  /** Whether the drawer is visible (open). */
  open: boolean;
  /** Which edge the drawer slides in from. */
  side: "left" | "right";
  /** Callback to close the drawer. */
  onClose: () => void;
  /** Accessible label for role="dialog". */
  label: string;
  children: ReactNode;
}

// ─── Component ─────────────────────────────────────────────────────────────────

export function PanelDrawer({ open, side, onClose, label, children }: PanelDrawerProps) {
  const drawerRef = useRef<HTMLDivElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  // Save previous focus on open; restore it on close.
  useEffect(() => {
    if (open) {
      previousFocusRef.current = document.activeElement as HTMLElement | null;
      // Defer focus so the drawer is in the rendered DOM first.
      const id = setTimeout(() => drawerRef.current?.focus(), 0);
      return () => clearTimeout(id);
    } else {
      previousFocusRef.current?.focus();
      previousFocusRef.current = null;
      return undefined;
    }
  }, [open]);

  // Esc close + Tab trap.
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key === "Tab" && drawerRef.current) {
        const focusable = getFocusable(drawerRef.current);
        if (focusable.length === 0) return;
        // Array is non-empty (guarded above); index access is safe.
        // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
        const first = focusable.at(0)!;
        // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
        const last = focusable.at(-1)!;
        if (e.shiftKey) {
          if (document.activeElement === first) {
            e.preventDefault();
            last.focus();
          }
        } else {
          if (document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
      }
    },
    [onClose],
  );

  const translateClosed = side === "left" ? "translateX(-100%)" : "translateX(100%)";

  const content = (
    <>
      {/* Backdrop */}
      <div
        data-testid="panel-drawer-backdrop"
        aria-hidden="true"
        onClick={onClose}
        className="panel-drawer-backdrop"
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0,0,0,0.4)",
          zIndex: 300,
          opacity: open ? 1 : 0,
          pointerEvents: open ? "auto" : "none",
          transition: "opacity 0.25s ease",
        }}
      />

      {/* Drawer panel */}
      <div
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        data-testid="panel-drawer"
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className={`panel-drawer panel-drawer--${side}`}
        style={{
          position: "fixed",
          top: 0,
          bottom: 0,
          [side]: 0,
          width: "min(80%, 360px)",
          background: "var(--syn-bg-soft)",
          borderRight: side === "left" ? "1px solid var(--syn-border)" : undefined,
          borderLeft: side === "right" ? "1px solid var(--syn-border)" : undefined,
          zIndex: 301,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          transform: open ? "translateX(0)" : translateClosed,
          visibility: open ? "visible" : "hidden",
          transition: "transform 0.25s ease, visibility 0.25s",
          outline: "none",
          // iOS safe-area insets inside the drawer
          paddingTop: "env(safe-area-inset-top, 0px)",
          paddingBottom: "env(safe-area-inset-bottom, 0px)",
          ...(side === "left"
            ? { paddingLeft: "env(safe-area-inset-left, 0px)" }
            : { paddingRight: "env(safe-area-inset-right, 0px)" }),
        }}
      >
        {children}
      </div>
    </>
  );

  if (typeof document === "undefined") return null;
  return createPortal(content, document.body);
}
