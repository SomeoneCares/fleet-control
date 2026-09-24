import { describe, expect, it } from "vitest";
import { NAV, featureOn } from "./Shell";

describe("workspace switches in the menu", () => {
  const messaging = NAV.flatMap((g) => g.items).find((i) => i.key === "messaging")!;

  it("hides Messaging only when the workspace switched it off", () => {
    expect(messaging.feature).toBe("messaging");
    expect(featureOn({ features: { messaging: true } }, messaging.feature)).toBe(true);
    expect(featureOn({ features: { messaging: false } }, messaging.feature)).toBe(false);
    expect(featureOn({}, messaging.feature)).toBe(true);  // an API from before the switch existed: on
    expect(featureOn({ features: { messaging: false } }, undefined)).toBe(true);  // items without a feature never hide
  });
});
