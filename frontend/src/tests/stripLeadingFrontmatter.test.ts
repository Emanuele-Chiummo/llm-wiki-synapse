import { describe, it, expect } from "vitest";

import { stripLeadingFrontmatter } from "../components/chat/renderMarkdown";

describe("stripLeadingFrontmatter", () => {
  it("removes a single leading frontmatter block", () => {
    const md = "---\ntype: concept\ntitle: X\n---\n\n# Body\ntext";
    expect(stripLeadingFrontmatter(md).trim()).toBe("# Body\ntext");
  });

  it("removes TWO consecutive leading blocks (legacy duplicated frontmatter)", () => {
    const md =
      "---\nlang: en\ntitle: X\n---\n\n---\ntype: concept\ntitle: X\n---\n\n# Body";
    expect(stripLeadingFrontmatter(md).trim()).toBe("# Body");
  });

  it("supports a `...` closing fence", () => {
    const md = "---\ntype: concept\n...\n# Body";
    expect(stripLeadingFrontmatter(md).trim()).toBe("# Body");
  });

  it("leaves content without frontmatter unchanged", () => {
    const md = "# No frontmatter\n\nJust text.";
    expect(stripLeadingFrontmatter(md)).toBe(md);
  });

  it("does NOT strip a `---` thematic break later in the body", () => {
    const md = "# Body\n\nSection A\n\n---\n\nSection B";
    expect(stripLeadingFrontmatter(md)).toBe(md);
  });

  it("leaves an unterminated leading `---` fence untouched", () => {
    const md = "---\ntype: concept\ntitle: no close\n\n# Body";
    expect(stripLeadingFrontmatter(md)).toBe(md);
  });
});
