import { describe, expect, it } from "vitest";

import { navigateFallbackDenylist } from "../pwa/navigateDenylist";

// Workbox NavigationRoute tests the denylist against url.pathname + url.search.
function denied(prefixes: string[], pathnameAndSearch: string): boolean {
  return navigateFallbackDenylist(prefixes).some((r) => r.test(pathnameAndSearch));
}

describe("navigateFallbackDenylist", () => {
  const prefixes = ["/authorize", "/token", "/register", "/.well-known", "/mcp"];

  it("denies an OAuth navigation that carries a query string", () => {
    expect(
      denied(
        prefixes,
        "/authorize?response_type=code&client_id=x&redirect_uri=https%3A%2F%2Fclaude.ai%2Fcb&code_challenge=y&code_challenge_method=S256&state=z",
      ),
    ).toBe(true);
  });

  it("denies bare prefixes and sub-paths", () => {
    expect(denied(prefixes, "/authorize")).toBe(true);
    expect(denied(prefixes, "/mcp/server")).toBe(true);
    expect(denied(prefixes, "/.well-known/oauth-protected-resource/mcp/server")).toBe(true);
  });

  it("does not deny SPA routes that merely start with the same letters", () => {
    expect(denied(prefixes, "/authorized-apps")).toBe(false);
    expect(denied(prefixes, "/tokens?tab=1")).toBe(false);
    expect(denied(prefixes, "/")).toBe(false);
  });

  it("escapes the dot in /.well-known", () => {
    expect(denied(prefixes, "/xwell-known")).toBe(false);
  });
});
