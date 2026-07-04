/**
 * viewport.ts — Breakpoint constants and media-query strings (ADR-0057).
 *
 * Single source of truth for TypeScript. The matching CSS breakpoints
 * are documented at the top of the responsive section in theme.css.
 *
 * Tier      | Condition                                  | Devices
 * ----------|--------------------------------------------|-------------------------------
 * mobile    | width ≤ MOBILE_MAX (767px)                 | iPhone portrait/landscape ≤740px
 * tablet    | MOBILE_MAX+1 – TABLET_MAX (768–1023px)     | iPad portrait (768/810/834px)
 * desktop   | width ≥ TABLET_MAX+1 (≥1024px)             | iPad landscape, desktop
 */

export const MOBILE_MAX = 767;
export const TABLET_MAX = 1023;

/** matchMedia string for the mobile tier (≤ 767px). */
export const MOBILE_QUERY = `(max-width: ${MOBILE_MAX}px)`;

/** matchMedia string for the tablet tier (768–1023px). */
export const TABLET_QUERY = `(min-width: ${MOBILE_MAX + 1}px) and (max-width: ${TABLET_MAX}px)`;
