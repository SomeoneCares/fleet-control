import { describe, expect, it } from "vitest";
import { lifetimeChoices, tokenState } from "./tokens";

describe("tokenState", () => {
  const now = 1_000;
  it("says revoked before expired", () => {
    expect(tokenState({ expires_at: 10, revoked_at: 5 }, now).label).toBe("Revoked");
    expect(tokenState({ expires_at: 10, revoked_at: null }, now)).toEqual({ label: "Expired", tone: "warning" });
    expect(tokenState({ expires_at: 2_000, revoked_at: null }, now)).toEqual({ label: "Active", tone: "success" });
  });
});

describe("lifetimeChoices", () => {
  it("never offers more than the workspace allows, and always offers the maximum", () => {
    expect(lifetimeChoices(90)).toEqual([7, 30, 90]);
    expect(lifetimeChoices(45)).toEqual([7, 30, 45]);
    expect(lifetimeChoices(3)).toEqual([3]);
  });
});
