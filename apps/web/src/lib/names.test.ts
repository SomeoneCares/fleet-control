import { describe, expect, it } from "vitest";
import { BLUEPRINT_NAME, suggestBlueprintName } from "./names";

describe("suggestBlueprintName", () => {
  it("keeps a plain instance id readable", () => {
    expect(suggestBlueprintName("hermesbo-lab-01")).toBe("hermesbo-lab-01-imported");
  });

  it("always yields a name the schema accepts", () => {
    for (const id of ["hermes.prod.eu", "01-lab", "a", "x".repeat(70), "Lab..01"]) {
      const name = suggestBlueprintName(id);
      expect(name, id).toMatch(BLUEPRINT_NAME);
    }
    expect(suggestBlueprintName("hermes.prod.eu")).toBe("hermes-prod-eu-imported");
    expect(suggestBlueprintName("01-lab")).toBe("fleet-01-lab-imported");
  });
});
