import { describe, expect, it } from "vitest";
import type { FleetOutput } from "../api/client";
import { KIND_ICON, KIND_LABEL, casesOf, matchesQuery } from "./outputs";

const out = (over: Partial<FleetOutput> = {}): FleetOutput => ({
  id: "out_1", zone: "case-files", name: "SAR draft v1.docx", kind: "document", classification: "restricted",
  produced_by: "sar-drafter", case: "AML-2026-0412", blueprint: "aml", instance_id: "prod-01",
  source: { kind: "agent", ref: "run_9" }, size: 12, at: 10, provenance: "sar-drafter produced it from an agent run (run_9) on prod-01",
  ...over,
});

describe("fleet outputs view logic", () => {
  it("filters on name, agent, case, zone and provenance", () => {
    for (const q of ["", "SAR", "sar-drafter", "0412", "case-files", "run_9"]) expect(matchesQuery(out(), q), q).toBe(true);
    expect(matchesQuery(out(), "kyc")).toBe(false);
  });

  it("lists the cases in order, without repeats or blanks", () => {
    expect(casesOf([out(), out({ id: "b", case: null }), out({ id: "c", case: "KYC-1" }), out({ id: "d" })]))
      .toEqual(["AML-2026-0412", "KYC-1"]);
  });

  it("labels every kind", () => {
    expect(KIND_LABEL.structured).toBe("Structured result");
    expect(Object.keys(KIND_ICON)).toEqual(["document", "structured", "graph", "markdown", "summary"]);
  });
});
