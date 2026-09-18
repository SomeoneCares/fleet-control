import type { DecisionRoomRow, EvidenceKind, RoomBase, RoomOption } from "../api/client";
import type { IconName } from "../components/ui";
import type { Tone } from "./view";

export const EVIDENCE_ICON: Record<EvidenceKind, IconName> = {
  file: "file",
  output: "folder",
  claim: "shield",
  note: "chat",
};

export const EVIDENCE_LABEL: Record<EvidenceKind, string> = {
  file: "File",
  output: "Fleet output",
  claim: "Assurance claim",
  note: "Note",
};

/** A room's state, said the way the room itself reports it: a split decision is not hidden. */
export function statusChip(room: Pick<RoomBase, "status" | "outcome">): { label: string; tone: Tone } {
  if (room.status === "cancelled") return { label: "Cancelled", tone: "neutral" };
  if (room.status === "decided") {
    return room.outcome && !room.outcome.agreed
      ? { label: "Decided · split", tone: "warning" }
      : { label: "Decided", tone: "success" };
  }
  return { label: "Open", tone: "info" };
}

/** How close the due date is, for rooms that have one. */
export function dueLabel(dueAt: number | null, now = Date.now() / 1000): { text: string; tone: Tone } | null {
  if (!dueAt) return null;
  const hours = (dueAt - now) / 3600;
  if (hours <= 0) return { text: "Overdue", tone: "error" };
  if (hours < 24) return { text: `Due in ${Math.max(1, Math.round(hours))} h`, tone: "warning" };
  const days = Math.round(hours / 24);
  return { text: `Due in ${days} day${days === 1 ? "" : "s"}`, tone: "neutral" };
}

/** Who the room is still waiting for, in words. Null once it is closed. */
export function waitingLabel(room: Pick<RoomBase, "status" | "waiting_for">): string | null {
  if (room.status !== "open") return null;
  const [who] = room.waiting_for;
  if (!who) return null;
  return who === "anyone who may decide" ? "Waiting for a decision" : `Waiting for ${who}`;
}

/** Only the full room carries its options; a list row reads a chosen option from `outcome.option`. */
export function optionLabel(room: { options: RoomOption[] }, id: string): string {
  return room.options.find((o) => o.id === id)?.label ?? id;
}

export function matchesQuery(room: Pick<RoomBase, "question" | "case" | "zone" | "opened_by">, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [room.question, room.case ?? "", room.zone, room.opened_by].join(" ").toLowerCase().includes(q);
}

/** The cases present in a list, newest room first, for the Case filter. */
export function casesOf(rooms: Pick<RoomBase, "case">[]): string[] {
  const seen: string[] = [];
  for (const r of rooms) if (r.case && !seen.includes(r.case)) seen.push(r.case);
  return seen;
}

/**
 * My decisions, in three piles: what waits for me, what I have already decided,
 * and what is open but not mine to decide (a second approver, or a role that never decides).
 */
export function decisionQueue(rows: DecisionRoomRow[]): {
  waiting: DecisionRoomRow[];
  decided: DecisionRoomRow[];
  elsewhere: DecisionRoomRow[];
} {
  return {
    waiting: rows.filter((r) => r.may_decide),
    decided: rows.filter((r) => r.mine),
    elsewhere: rows.filter((r) => r.status === "open" && !r.may_decide && !r.mine),
  };
}
