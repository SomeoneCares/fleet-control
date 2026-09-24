import { describe, expect, it } from "vitest";
import type { DecisionRoomRow } from "../api/client";
import {
  BASIS_LABEL, EVIDENCE_BASES, EVIDENCE_ICON, EVIDENCE_LABEL, basisCounts, casesOf, decisionQueue, dueLabel, matchesQuery, optionLabel, statusChip, waitingLabel,
} from "./rooms";

const OPTIONS = [
  { id: "file-sar", label: "File SAR draft for signature" },
  { id: "more-evidence", label: "Request more evidence" },
];

const room = (over: Partial<DecisionRoomRow> = {}): DecisionRoomRow => ({
  id: "room_1", question: "Should we file a SAR for Alpha Trading Ltd?", case: "AML-2026-0412", zone: "case-files",
  status: "open", second_approver: null, opened_by: "case-orchestrator", opened_by_kind: "agent",
  instance_id: "prod-01", due_at: null, created_at: 100, updated_at: 100, closed_at: null, mine: false,
  may_decide: true, reason: null, waiting_for: ["anyone who may decide"], outcome: null,
  evidence: 5, findings: 3, decisions: 0, ...over,
});

const NOW = 1_000_000;

describe("decision room view logic", () => {
  it("labels every evidence kind with an icon that exists", () => {
    expect(Object.keys(EVIDENCE_ICON)).toEqual(["file", "output", "claim", "note"]);
    expect(EVIDENCE_LABEL.claim).toBe("Assurance claim");
    expect(EVIDENCE_ICON.note).toBe("chat");
  });

  it("says when a decision was split instead of hiding it", () => {
    expect(statusChip(room())).toEqual({ label: "Open", tone: "info" });
    expect(statusChip(room({ status: "cancelled" }))).toEqual({ label: "Cancelled", tone: "neutral" });
    const agreed = { option: OPTIONS[0], agreed: true, decisions: 2 };
    expect(statusChip(room({ status: "decided", outcome: agreed }))).toEqual({ label: "Decided", tone: "success" });
    expect(statusChip(room({ status: "decided", outcome: { ...agreed, agreed: false } })))
      .toEqual({ label: "Decided · split", tone: "warning" });
  });

  it("turns a due date into how close it is", () => {
    expect(dueLabel(null, NOW)).toBe(null);
    expect(dueLabel(NOW - 60, NOW)).toEqual({ text: "Overdue", tone: "error" });
    expect(dueLabel(NOW + 3 * 3600, NOW)).toEqual({ text: "Due in 3 h", tone: "warning" });
    expect(dueLabel(NOW + 24 * 3600, NOW)).toEqual({ text: "Due in 1 day", tone: "neutral" });
    expect(dueLabel(NOW + 72 * 3600, NOW)).toEqual({ text: "Due in 3 days", tone: "neutral" });
  });

  it("names who the room waits for, and stops once it is closed", () => {
    expect(waitingLabel(room())).toBe("Waiting for a decision");
    expect(waitingLabel(room({ waiting_for: ["marcus@example.org"] }))).toBe("Waiting for marcus@example.org");
    expect(waitingLabel(room({ status: "decided", waiting_for: [] }))).toBe(null);
    expect(waitingLabel(room({ waiting_for: [] }))).toBe(null);
  });

  it("falls back to the option id when an option is gone", () => {
    // A list row has no options at all (the API sends them with the room itself), so this takes the full room.
    expect(optionLabel({ options: OPTIONS }, "file-sar")).toBe("File SAR draft for signature");
    expect(optionLabel({ options: OPTIONS }, "vanished")).toBe("vanished");
  });

  it("searches question, case, zone and who opened it", () => {
    for (const q of ["", "SAR", "0412", "case-files", "orchestrator"]) expect(matchesQuery(room(), q), q).toBe(true);
    expect(matchesQuery(room(), "payroll")).toBe(false);
  });

  it("lists the cases in order, without repeats or blanks", () => {
    expect(casesOf([room(), room({ case: null }), room({ case: "KYC-1" }), room()])).toEqual(["AML-2026-0412", "KYC-1"]);
  });

  it("splits my decisions into what waits for me, what I decided, and what is elsewhere", () => {
    const waiting = room({ id: "a" });
    const decided = room({ id: "b", status: "decided", may_decide: false, mine: true });
    const elsewhere = room({ id: "c", may_decide: false, reason: "Waiting for the second approver (marcus@example.org)." });
    const closedNotMine = room({ id: "d", status: "decided", may_decide: false });
    const q = decisionQueue([waiting, decided, elsewhere, closedNotMine]);
    expect(q.waiting.map((r) => r.id)).toEqual(["a"]);
    expect(q.decided.map((r) => r.id)).toEqual(["b"]);
    expect(q.elsewhere.map((r) => r.id)).toEqual(["c"]);
  });

  it("says what each item rests on, and never offers a judgment for anything but a person's note", () => {
    const items = [{ basis: "analytical" as const }, { basis: "source" as const }, { basis: "analytical" as const }, { basis: null }, {}];
    expect(basisCounts(items)).toEqual([{ basis: "source", count: 1 }, { basis: "analytical", count: 2 }]);
    expect(BASIS_LABEL.interpretation).toBe("Agent interpretation");
    const judged = Object.entries(EVIDENCE_BASES).filter(([, bases]) => bases.includes("judgment")).map(([kind]) => kind);
    expect(judged).toEqual(["note"]);
    expect(EVIDENCE_BASES.claim).toEqual([]);
  });
});
