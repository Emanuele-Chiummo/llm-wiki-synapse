/**
 * renderMarkdownEmptyGuard.test.ts — R11-4 BUG1 / G3.
 *
 * renderMarkdown must guard empty/nullish input and return "" WITHOUT running the
 * marked/DOMPurify pipeline or tripping the dev double-call tracker. This is what an
 * empty preview pane or a not-yet-settled chat message passes in; before the guard it
 * produced spurious console noise (AC-R11-4-BUG1).
 */

import { describe, it, expect } from "vitest";
import { renderMarkdown } from "../components/chat/renderMarkdown";

describe("renderMarkdown empty/nullish guard (R11-4 BUG1)", () => {
  it("returns '' for an empty string without throwing", () => {
    expect(renderMarkdown("")).toBe("");
  });

  it("returns '' for whitespace-only input without throwing", () => {
    expect(renderMarkdown("   \n\t  ")).toBe("");
  });

  it("returns '' for a nullish value passed as string without throwing", () => {
    expect(renderMarkdown(null as unknown as string)).toBe("");
    expect(renderMarkdown(undefined as unknown as string)).toBe("");
  });

  it("still renders real content (guard does not swallow non-empty input)", () => {
    const html = renderMarkdown("hello **world**");
    expect(html).toContain("<strong>world</strong>");
  });
});
