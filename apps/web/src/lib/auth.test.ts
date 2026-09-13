import { describe, expect, it } from "vitest";
import type { Me } from "../api/client";
import { approvalFor, canApply, initials } from "./view";

const PERMS: Record<Me["role"], string[]> = {
  admin: ["plans.approve.nonprod", "plans.approve.production", "plans.apply.nonprod", "plans.apply.production"],
  fleet_architect: ["plans.approve.nonprod", "plans.apply.nonprod"],
  operator: ["plans.approve.nonprod", "plans.apply.nonprod", "plans.apply.production"],
  approver: ["plans.approve.production"],
  viewer: [],
};

function me(role: Me["role"], email = `${role}@x`): Me {
  return { email, name: role, role, role_label: role, portal: "admin", permissions: PERMS[role] };
}

const prod = { environment: "production" as const, approvals: [] as string[], approvals_required: 2, status: "planned" as const, created_by: "dana@x" };

describe("approval rules in the UI", () => {
  it("lets Admin and Approver approve production, not the creator, once each", () => {
    expect(approvalFor(prod, me("approver")).can).toBe(true);
    expect(approvalFor(prod, me("admin")).can).toBe(true);
    expect(approvalFor(prod, me("fleet_architect")).reason).toMatch(/Admin or an Approver/);
    expect(approvalFor(prod, me("admin", "dana@x")).reason).toMatch(/you created this plan/i);
    expect(approvalFor({ ...prod, approvals: ["approver@x"] }, me("approver")).reason).toMatch(/you have approved/i);
    expect(approvalFor({ ...prod, approvals: ["a", "b"] }, me("admin"))).toEqual({ can: false, reason: null });
  });

  it("lets architects and operators approve staging", () => {
    const stg = { ...prod, environment: "staging" as const, approvals_required: 1 };
    expect(approvalFor(stg, me("operator")).can).toBe(true);
    expect(approvalFor(stg, me("fleet_architect")).can).toBe(true);
    expect(approvalFor(stg, me("approver")).can).toBe(false);
  });

  it("knows who may apply", () => {
    expect(canApply({ environment: "production" }, me("fleet_architect"))).toBe(false);
    expect(canApply({ environment: "production" }, me("operator"))).toBe(true);
    expect(canApply({ environment: "staging" }, me("fleet_architect"))).toBe(true);
    expect(canApply({ environment: "staging" }, me("viewer"))).toBe(false);
  });

  it("makes initials", () => {
    expect(initials("Dana Whitfield")).toBe("DW");
    expect(initials("dana.whitfield@fleetcontrol.local")).toBe("DW");
    expect(initials("admin@x")).toBe("AD");
  });
});
