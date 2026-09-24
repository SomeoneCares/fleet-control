import { describe, expect, it } from "vitest";
import type { AskSource } from "../api/client";
import { answerParts, outputName, sourceLink, splitSources } from "./ask";

const src = (id: string, kind: AskSource["kind"] = "file"): AskSource =>
  ({ id, key: `${kind}:${id}`, kind, ref: `r-${id}`, label: id, zone: "case-files", classification: null, chars: 10, truncated: false });

describe("ask view logic", () => {
  it("splits an answer into text and citations", () => {
    expect(answerParts("Two cases [S1][S2]. Done")).toEqual([{ text: "Two cases " }, { cite: "S1" }, { cite: "S2" }, { text: ". Done" }]);
    expect(answerParts("No citations")).toEqual([{ text: "No citations" }]);
  });

  it("tells the sources used from the ones only sent", () => {
    const { used, unused } = splitSources({ sources: [src("S1"), src("S2"), src("S3")], cited: ["S2"] });
    expect(used.map((s) => s.id)).toEqual(["S2"]);
    expect(unused.map((s) => s.id)).toEqual(["S1", "S3"]);
  });

  it("links each kind of source to where it lives", () => {
    expect(sourceLink(src("S1", "room"))).toBe("/rooms/r-S1");
    expect(sourceLink(src("S1", "output"))).toBe("/outputs?id=r-S1");
    expect(sourceLink(src("S1"))).toBe("/content?zone=case-files&file=r-S1");
  });

  it("names a saved answer after its question", () => {
    expect(outputName("Which open cases touch Cyprus?")).toBe("Which open cases touch Cyprus");
    expect(outputName("x".repeat(100)).length).toBeLessThanOrEqual(78);
  });
});
