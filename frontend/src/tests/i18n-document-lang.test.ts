import { afterEach, describe, expect, it } from "vitest";

import i18n from "../i18n";

describe("document language", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("tracks the active application locale for assistive technology", async () => {
    await i18n.changeLanguage("it");
    expect(document.documentElement.lang).toBe("it");
  });
});
