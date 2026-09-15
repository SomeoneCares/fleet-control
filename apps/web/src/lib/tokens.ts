import type { ApiToken } from "../api/client";
import type { Tone } from "./view";

/** What an API token row says about itself. */
export function tokenState(t: Pick<ApiToken, "expires_at" | "revoked_at">, now: number): { label: "Active" | "Expired" | "Revoked"; tone: Tone } {
  if (t.revoked_at !== null) return { label: "Revoked", tone: "neutral" };
  if (t.expires_at <= now) return { label: "Expired", tone: "warning" };
  return { label: "Active", tone: "success" };
}

/** Lifetimes offered for a new token, capped by Settings → General (the API enforces the same cap). */
export function lifetimeChoices(maxDays: number): number[] {
  const out = [7, 30, 90, 180, 365].filter((d) => d <= maxDays);
  if (!out.includes(maxDays)) out.push(maxDays);
  return out;
}
