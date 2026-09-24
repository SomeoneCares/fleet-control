import type { AccessPermission } from "../api/client";

// The order the portal's menu uses, so the inspector reads the same way.
const AREA_ORDER = ["Workspace", "Design", "Plans", "Operate", "Estate", "Govern", "Settings", "Other"];

/** Permissions grouped by portal area, areas in menu order, each area keeping the API's order. */
export function groupByArea(perms: AccessPermission[]): [string, AccessPermission[]][] {
  const groups = new Map<string, AccessPermission[]>();
  for (const p of perms) groups.set(p.area, [...(groups.get(p.area) ?? []), p]);
  return [...groups.entries()].sort(([a], [b]) => {
    const ia = AREA_ORDER.indexOf(a), ib = AREA_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
}
