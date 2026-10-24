/**
 * Service-worker navigateFallback denylist (vite-plugin-pwa / Workbox).
 *
 * Workbox's NavigationRoute tests each RegExp against ``url.pathname + url.search``, not the
 * bare pathname. A prefix pattern ending in ``(\/|$)`` therefore misses every navigation that
 * carries a query string, and the SW answered it with the cached index.html: exactly what
 * happened to the OAuth ``/authorize?response_type=code&...`` redirect from claude.ai, which
 * showed the SPA's setup wizard instead of the backend consent page (2.1.21). The boundary
 * after the prefix is a slash, a query string, or the end.
 */
export function navigateFallbackDenylist(prefixes: readonly string[]): RegExp[] {
  return prefixes.map(
    (p) => new RegExp(`^${p.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")}(\\/|\\?|$)`),
  );
}
