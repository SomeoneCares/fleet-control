import { describe, expect, it } from "vitest";
import type { GoamlSettings } from "../api/client";
import { GOAML_STATUS, checkedAgainstOlderSchema, goamlGaps } from "./goaml";

const settings: GoamlSettings = {
  profile: { fiu: "EMLCU (Egypt)", rentity_id: 1042, currency_code_local: "EGP", submission_code: "E" },
  schema: { id: "gxs_1", name: "goAMLSchema.xsd", sha256: "abc", loaded_at: 1, loaded_by: "a@x", size: 10 },
  draft_schema: "fleetcontrol.goaml-draft/v1",
};

describe("goAML view logic", () => {
  it("says what is missing before reports can be prepared", () => {
    expect(goamlGaps(settings)).toEqual([]);
    expect(goamlGaps({ ...settings, schema: null })).toEqual(["the FIU's goAML schema (Settings → goAML)"]);
    expect(goamlGaps(null)).toHaveLength(2);
  });

  it("notices a report checked against an older schema, but never a filed one", () => {
    expect(checkedAgainstOlderSchema({ status: "ready", schema: { name: "x", sha256: "abc" } }, settings)).toBe(false);
    expect(checkedAgainstOlderSchema({ status: "invalid", schema: { name: "x", sha256: "old" } }, settings)).toBe(true);
    expect(checkedAgainstOlderSchema({ status: "unchecked", schema: null }, settings)).toBe(true);
    expect(checkedAgainstOlderSchema({ status: "filed", schema: { name: "x", sha256: "old" } }, settings)).toBe(false);
  });

  it("only a report that passed the schema reads as ready", () => {
    expect(GOAML_STATUS.ready.tone).toBe("success");
    expect(GOAML_STATUS.unchecked.tone).not.toBe("success");
  });
});
