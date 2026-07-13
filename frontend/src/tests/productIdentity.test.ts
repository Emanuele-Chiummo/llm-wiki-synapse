import { describe, expect, it } from "vitest";

import { PRODUCT_IDENTITY } from "../config/productIdentity";

describe("PRODUCT_IDENTITY", () => {
  it("keeps the public name and product positioning in one rename-ready contract", () => {
    expect(PRODUCT_IDENTITY).toEqual({
      displayName: "Synapse",
      descriptor: "The self-hosted LLM wiki that turns your sources into connected knowledge.",
      tagline: "Connect everything.",
    });
  });
});
