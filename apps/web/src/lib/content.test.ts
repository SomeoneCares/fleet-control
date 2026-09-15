import { describe, expect, it } from "vitest";
import { CLASSIFICATION_TONE, fileIcon, formatSize, readersLabel, zoneIdFrom } from "./content";

describe("content view logic", () => {
  it("turns a zone name into the id the API will use", () => {
    expect(zoneIdFrom("Case files")).toBe("case-files");
    expect(zoneIdFrom("Policies & procedures")).toBe("policies-procedures");
    expect(zoneIdFrom("9")).toBe("");
    expect(zoneIdFrom("")).toBe("");
  });

  it("colours classifications by how sensitive they are", () => {
    expect([CLASSIFICATION_TONE.internal, CLASSIFICATION_TONE.confidential, CLASSIFICATION_TONE.restricted])
      .toEqual(["neutral", "warning", "error"]);
  });

  it("shows sizes and picks an icon per file type", () => {
    expect([formatSize(null), formatSize(512), formatSize(2048), formatSize(3_000_000)]).toEqual(["—", "512 B", "2 kB", "2.9 MB"]);
    expect([fileIcon("wire log.csv"), fileIcon("brief.md"), fileIcon("graph.svg")]).toEqual(["layers", "file", "graph"]);
  });

  it("names the readers with Admins always first", () => {
    expect(readersLabel({ read_roles: ["approver", "viewer"] }, (r) => (r === "admin" ? "Admin" : r === "approver" ? "Approver" : "Viewer")))
      .toBe("Admins, Approvers, Viewers");
    expect(readersLabel({ read_roles: [] }, () => "Admin")).toBe("Admins");
  });
});
