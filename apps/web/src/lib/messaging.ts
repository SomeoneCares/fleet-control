import type { Channel, ChannelStatus, Delivery, DeliveryRuleRow } from "../api/client";
import type { Tone } from "./view";

export const CHANNEL_STATUS: Record<ChannelStatus, { label: string; tone: Tone }> = {
  ready: { label: "Ready", tone: "success" },
  unknown: { label: "Not discovered", tone: "neutral" },
  disabled: { label: "Off", tone: "neutral" },
  webhooks_off: { label: "Webhooks off", tone: "warning" },
  platform_missing: { label: "Not configured", tone: "warning" },
  platform_down: { label: "Platform down", tone: "error" },
  route_missing: { label: "Route missing", tone: "error" },
  route_off: { label: "Route off", tone: "warning" },
};

export const DELIVERY_TONE: Record<Delivery["status"], Tone> = { queued: "neutral", sent: "info", delivered: "success", failed: "error" };

export const TEMPLATE_LABEL: Record<string, string> = {
  "decision-request": "Decision request",
  "approval-request": "Approval request",
  "output-summary": "Output summary",
  alert: "Alert",
};

/** Where a rule's message links to, by event, as the Messaging design lists it. */
export const EVENT_LINK: Record<string, string> = {
  "decision_room.opened": "Opens the room",
  "approval.second_needed": "Opens the room",
  "output.shared": "Opens Fleet outputs",
  "assurance.no_evidence": "Opens Assurance",
  "assurance.policy_blocked": "Opens Assurance",
  "drift.detected": "Opens the instance's drift",
  "apply.completed": "Opens the plan",
  "apply.failed": "Opens the plan",
};

/** A channel's failures today, from the recent deliveries. */
export function failuresToday(channel: Pick<Channel, "id">, deliveries: Delivery[], now = Date.now() / 1000): number {
  const midnight = new Date(now * 1000);
  midnight.setHours(0, 0, 0, 0);
  return deliveries.filter((d) => d.channel === channel.id && d.status === "failed" && d.at >= midnight.getTime() / 1000).length;
}

/** "4 of 5 enabled", and how many rules name a channel Fleet Control does not have. */
export function ruleSummary(rules: DeliveryRuleRow[]): { enabled: number; total: number; orphaned: number } {
  return { enabled: rules.filter((r) => r.enabled).length, total: rules.length, orphaned: rules.filter((r) => !r.channel).length };
}

/** A channel's name from the id a string like "Ops on-call" becomes, so people see the ref before creating it. */
export function channelIdFrom(name: string): string {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 41).replace(/-+$/, "");
}
