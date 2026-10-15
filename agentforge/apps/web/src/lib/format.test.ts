import { describe, expect, it } from "vitest";

import { columnLetter, formatAge, formatBytes, formatPages } from "./format";

describe("format", () => {
  it("formats sizes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(25 * 1024 * 1024)).toBe("25 MB");
  });

  it("formats page ranges", () => {
    expect(formatPages(3, 3)).toBe("p. 3");
    expect(formatPages(3, 5)).toBe("pp. 3–5");
  });

  it("names spreadsheet columns", () => {
    expect([0, 1, 25, 26, 27, 51, 52, 701, 702].map(columnLetter)).toEqual([
      "A",
      "B",
      "Z",
      "AA",
      "AB",
      "AZ",
      "BA",
      "ZZ",
      "AAA",
    ]);
  });

  it("formats ages", () => {
    const now = new Date("2026-10-13T12:00:00Z");
    expect(formatAge("2026-10-13T11:59:30Z", now)).toBe("just now");
    expect(formatAge("2026-10-13T11:45:00Z", now)).toBe("15 min ago");
    expect(formatAge("2026-10-13T09:00:00Z", now)).toBe("3 h ago");
  });
});
