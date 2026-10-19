import { describe, expect, it } from "vitest";

import { type ApiKey, keyState } from "./api-keys";

const base: ApiKey = {
  id: "k",
  name: "CI",
  prefix: "abcde12345",
  scopes: ["api:read"],
  created_by_email: "admin@example.test",
  created_at: "2026-10-01T00:00:00Z",
  expires_at: "2026-12-30T00:00:00Z",
  last_used_at: null,
  revoked_at: null,
};

describe("keyState", () => {
  const now = new Date("2026-10-18T12:00:00Z");
  it("is active until it expires or is revoked", () => {
    expect(keyState(base, now)).toBe("active");
    expect(keyState({ ...base, expires_at: "2026-10-18T11:59:59Z" }, now)).toBe("expired");
    expect(keyState({ ...base, revoked_at: "2026-10-17T00:00:00Z" }, now)).toBe("revoked");
  });
});
