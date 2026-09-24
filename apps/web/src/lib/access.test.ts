import { describe, expect, it } from "vitest";
import type { AccessPermission } from "../api/client";
import { groupByArea } from "./access";

const p = (permission: string, area: string): AccessPermission => ({ permission, area, label: permission, allowed: true, why: "" });

describe("access inspector view logic", () => {
  it("groups permissions by area in menu order, keeping their order within an area", () => {
    const groups = groupByArea([p("settings.read", "Settings"), p("rooms.read", "Workspace"), p("x", "Mystery"), p("rooms.decide", "Workspace")]);
    expect(groups.map(([area, ps]) => [area, ps.map((x) => x.permission)])).toEqual([
      ["Workspace", ["rooms.read", "rooms.decide"]], ["Settings", ["settings.read"]], ["Mystery", ["x"]],
    ]);
  });
});
