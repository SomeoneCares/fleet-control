import { describe, expect, it } from "vitest";
import type { Delivery, DeliveryRuleRow } from "../api/client";
import { CHANNEL_STATUS, channelIdFrom, failuresToday, ruleSummary } from "./messaging";

const d = (channel: string, status: Delivery["status"], at: number): Delivery =>
  ({ id: `${channel}${at}`, channel, instance_id: "lab", event: "test", key: "k", text: "", status, error: null, at, finished_at: null, rule: null, by: null });

describe("messaging view logic", () => {
  it("counts a channel's failures since midnight only", () => {
    const now = new Date(2026, 8, 24, 15, 0, 0).getTime() / 1000;
    const yesterday = new Date(2026, 8, 23, 23, 0, 0).getTime() / 1000;
    const deliveries = [d("ops", "failed", now - 60), d("ops", "failed", yesterday), d("ops", "delivered", now - 30), d("mail", "failed", now - 10)];
    expect(failuresToday({ id: "ops" }, deliveries, now)).toBe(1);
  });

  it("summarises rules and the ones naming no channel", () => {
    const r = (enabled: boolean, channel: string | null): DeliveryRuleRow =>
      ({ blueprint: "aml", version: 3, n: 0, when: "drift.detected", event: "Drift detected", to: "x:y", template: "alert", enabled, channel });
    expect(ruleSummary([r(true, "ops"), r(false, "ops"), r(true, null)])).toEqual({ enabled: 2, total: 3, orphaned: 1 });
  });

  it("shows the id a channel name becomes, as the API makes it", () => {
    expect(channelIdFrom(" Ops on-call ")).toBe("ops-on-call");
    expect(channelIdFrom("#fleet-ops!!")).toBe("fleet-ops");
  });

  it("has a label for every channel status", () => {
    expect(Object.keys(CHANNEL_STATUS).sort()).toEqual(
      ["disabled", "platform_down", "platform_missing", "ready", "route_missing", "route_off", "unknown", "webhooks_off"]);
  });
});
