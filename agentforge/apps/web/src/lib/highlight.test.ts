import { describe, expect, it } from "vitest";

import { highlightQuote } from "./highlight";

const PASSAGE =
  "## Logging\n\nAccess logs are retained for\nninety days. Logs are **reviewed weekly**.";

describe("highlightQuote", () => {
  it("highlights a quote across line breaks and case", () => {
    const segments = highlightQuote(PASSAGE, "access logs are retained for ninety days.");
    expect(segments.find((s) => s.highlight)?.text).toBe(
      "Access logs are retained for\nninety days",
    );
    expect(segments.map((s) => s.text).join("")).toBe(PASSAGE);
  });

  it("matches through Markdown emphasis and curly quotes", () => {
    expect(highlightQuote(PASSAGE, "Logs are reviewed weekly").some((s) => s.highlight)).toBe(true);
    const curly = highlightQuote("The “owner’s” key", '"owner\'s" key');
    expect(curly.some((s) => s.highlight)).toBe(true);
  });

  it("returns the passage unhighlighted when the quote is absent or empty", () => {
    expect(highlightQuote(PASSAGE, "data in transit")).toEqual([
      { text: PASSAGE, highlight: false },
    ]);
    expect(highlightQuote(PASSAGE, "  ")).toEqual([{ text: PASSAGE, highlight: false }]);
  });

  it("treats regex characters in the quote literally", () => {
    expect(highlightQuote("Cost is $5 (approx.)", "$5 (approx.)")[1]).toEqual({
      text: "$5 (approx.)",
      highlight: true,
    });
  });
});
