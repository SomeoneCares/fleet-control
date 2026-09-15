import type { Classification, ContentZone } from "../api/client";
import type { IconName } from "../components/ui";
import type { Tone } from "./view";

export const CLASSIFICATIONS: Classification[] = ["internal", "confidential", "restricted"];

export const CLASSIFICATION_TONE: Record<Classification, Tone> = {
  internal: "neutral",
  confidential: "warning",
  restricted: "error",
};

export const CLASSIFICATION_LABEL: Record<Classification, string> = {
  internal: "Internal",
  confidential: "Confidential",
  restricted: "Restricted",
};

/** Mirrors the API's zone id rule, so the dialog can show the id before saving. */
export function zoneIdFrom(name: string): string {
  const id = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 63).replace(/-+$/, "");
  return /^[a-z][a-z0-9-]{1,62}$/.test(id) ? id : "";
}

export function fileIcon(name: string): IconName {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (["json", "yaml", "yml", "csv", "xlsx"].includes(ext)) return "layers";
  if (["png", "jpg", "jpeg", "svg"].includes(ext)) return "graph";
  return "file";
}

export function formatSize(size: number | null): string {
  if (size === null) return "—";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 102.4) / 10} kB`;
  return `${Math.round(size / (1024 * 102.4)) / 10} MB`;
}

/** Who reads a zone, in words: "Admins, Approvers" (Admins always do). */
export function readersLabel(zone: Pick<ContentZone, "read_roles">, roleLabel: (r: string) => string): string {
  const roles = ["admin", ...zone.read_roles.filter((r) => r !== "admin")];
  return roles.map((r) => `${roleLabel(r)}s`).join(", ");
}
