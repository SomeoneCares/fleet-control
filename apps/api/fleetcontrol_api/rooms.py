"""Decision Rooms (build document §4.1, §9 Slice 4): a governed question, its evidence, and the decision.

A room asks one question about one case. Its evidence is what Fleet Control already holds — content files,
fleet outputs, assurance claims with their verdicts — plus notes a person adds; findings come from the agents
that worked the case. People decide by choosing an option and writing a rationale; decisions are append-only,
one per person, and a room that needs a second approver stays open until they have decided too. The room's
content zone decides who may see it, exactly as it does for files and outputs.

Every piece of evidence and every finding says what it rests on (its ``basis``), so a person deciding can tell
the source data from what an analytical system (SAS, say) computed, from an agent's reading of either, from an
assumption, from a person's judgment. Only people make judgments. An analytical finding names the tool that
computed it, and Fleet Control checks that tool against what the run actually called: one of the four verdicts,
never a guess.

Messaging delivers a room; it never records a decision (build document §8).
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

EVIDENCE_KINDS = ("file", "output", "claim", "note")
BASES = ("source", "analytical", "interpretation", "assumption", "judgment")
# what each kind of item may rest on, and what it rests on when nobody says; a claim is Fleet Control's own
# verification of a run, so it carries its verdict instead of a basis
_EVIDENCE_BASES = {"file": (("source", "analytical", "assumption"), "source"),
                   "output": (("analytical", "interpretation", "assumption"), "interpretation"),
                   "note": (("source", "assumption", "judgment"), "judgment"),
                   "claim": ((), None)}
FINDING_BASES = ("source", "analytical", "interpretation", "assumption")  # agents do not judge; people do
VERDICTS = ("Evidence found", "No evidence", "Not verifiable", "Policy blocked")
STATUSES = ("open", "decided", "cancelled")
_OPTION_ID = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


class RoomError(ValueError):
    pass


def option_id(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(label).strip().lower()).strip("-")[:41].rstrip("-")
    if not _OPTION_ID.match(slug):
        raise RoomError("an option needs a few words of text")
    return slug


def new_room(*, room_id: str, question: str, zone: str, options: list[str], opened_by: str, opened_by_kind: str, at: float,
             case: Optional[str] = None, due_at: Optional[float] = None, second_approver: Optional[str] = None,
             instance_id: Optional[str] = None) -> dict:
    question = (question or "").strip()
    if not (10 <= len(question) <= 300):
        raise RoomError("a question is between 10 and 300 characters")
    labels = [o.strip() for o in options if (o or "").strip()]
    if not (2 <= len(labels) <= 6):
        raise RoomError("a room offers between 2 and 6 options")
    ids = [option_id(o) for o in labels]
    if len(set(ids)) != len(ids):
        raise RoomError("two options are too alike to tell apart")
    if opened_by_kind not in ("person", "agent"):
        raise RoomError("a room is opened by a person or an agent")
    return {
        "id": room_id,
        "question": question,
        "case": (case or "").strip() or None,
        "zone": zone,
        "status": "open",
        "options": [{"id": i, "label": label} for i, label in zip(ids, labels)],
        "second_approver": (second_approver or "").strip().lower() or None,
        "opened_by": opened_by,
        "opened_by_kind": opened_by_kind,
        "instance_id": instance_id,
        "due_at": due_at,
        "created_at": at,
        "updated_at": at,
        "evidence": [],
        "findings": [],
        "decisions": [],
        "closed_at": None,
    }


def new_evidence(*, kind: str, label: str, ref: Optional[str] = None, source: Optional[str] = None,
                 verdict: Optional[str] = None, added_by: str, at: float, basis: Optional[str] = None) -> dict:
    if kind not in EVIDENCE_KINDS:
        raise RoomError(f"evidence is one of: {', '.join(EVIDENCE_KINDS)}")
    allowed, default = _EVIDENCE_BASES[kind]
    if basis is not None and basis not in allowed:
        raise RoomError(f"{kind} evidence rests on one of: {', '.join(allowed)}" if allowed
                        else "a claim carries its verdict, not a basis")
    label = (label or "").strip()
    if not label or len(label) > 300:
        raise RoomError("evidence needs a label of at most 300 characters")
    if verdict is not None and verdict not in VERDICTS:
        raise RoomError(f"a verdict is one of: {', '.join(VERDICTS)}")
    if kind in ("file", "output", "claim") and not ref:
        raise RoomError(f"{kind} evidence points at something: give its id")
    return {"kind": kind, "label": label, "ref": ref, "source": source, "verdict": verdict, "added_by": added_by, "at": at,
            "basis": basis or default}


def new_finding(*, agent: str, text: str, verdict: Optional[str] = None, at: float, run_id: Optional[str] = None,
                basis: Optional[str] = None, tool: Optional[str] = None, session_id: Optional[str] = None,
                tool_check: Optional[dict] = None) -> dict:
    text = (text or "").strip()
    if not text or len(text) > 2000:
        raise RoomError("a finding is between 1 and 2000 characters")
    if not (agent or "").strip():
        raise RoomError("a finding says which agent found it")
    if verdict is not None and verdict not in VERDICTS:
        raise RoomError(f"a verdict is one of: {', '.join(VERDICTS)}")
    basis = basis or "interpretation"
    if basis == "judgment":
        raise RoomError("agents do not make judgments; people do, when they decide")
    if basis not in FINDING_BASES:
        raise RoomError(f"a finding rests on one of: {', '.join(FINDING_BASES)}")
    tool = (tool or "").strip() or None
    if basis == "analytical" and not tool:
        raise RoomError("an analytical finding names the tool that computed it (for example sas-viya.run_model)")
    return {"agent": agent.strip(), "text": text, "verdict": verdict, "run_id": run_id, "at": at, "basis": basis,
            "tool": tool, "session_id": session_id, "tool_check": tool_check}


def check_tool(tool: str, *, run: Optional[dict] = None, events: Optional[list[dict]] = None) -> dict:
    """Did the run that produced a finding really call the tool the finding says computed it?

    ``run`` is a Test Lab run (its transcript's tool calls); ``events`` are the fleetcontrol plugin's tool events
    for the finding's session. Neither = Not verifiable: there is nothing to check against, and that is said."""
    from .testlab import tool_matches

    if run is not None:
        calls = run.get("tool_calls")
        if calls is None:
            return {"verdict": "Not verifiable", "detail": f"run {run.get('id')} has no transcript"}
        if any(tool_matches(tool, c.get("name") or "") for c in calls):
            return {"verdict": "Evidence found", "detail": f"run {run.get('id')} called {tool}"}
        return {"verdict": "No evidence", "detail": f"run {run.get('id')} never called {tool}"}
    if events:
        if any(e.get("kind") == "tool.pre" and e.get("decision") == "block" and tool_matches(tool, e.get("tool") or "") for e in events):
            return {"verdict": "Policy blocked", "detail": f"a Fleet Control policy blocked {tool} in this session"}
        if any(e.get("kind") == "tool.post" and tool_matches(tool, e.get("tool") or "") for e in events):
            return {"verdict": "Evidence found", "detail": f"the session called {tool}"}
        return {"verdict": "No evidence", "detail": f"the session's recorded tool calls do not include {tool}"}
    return {"verdict": "Not verifiable", "detail": "no run or session with recorded tool calls to check against"}


def decide(room: dict, *, by: str, option_id_: str, rationale: str, at: float) -> dict:
    """Record one person's decision. Append-only: nobody decides twice, and nothing is overwritten."""
    if room["status"] != "open":
        raise RoomError(f"this room is {room['status']}")
    if not any(o["id"] == option_id_ for o in room["options"]):
        raise RoomError("that option is not on this room")
    rationale = (rationale or "").strip()
    if len(rationale) < 10:
        raise RoomError("a decision carries a rationale of at least 10 characters")
    if any(d["by"] == by for d in room["decisions"]):
        raise RoomError("you have already decided on this room")
    room["decisions"].append({"by": by, "option": option_id_, "rationale": rationale, "at": at})
    room["updated_at"] = at
    if is_complete(room):
        room["status"] = "decided"
        room["closed_at"] = at
    return room


def is_complete(room: dict) -> bool:
    """One decision closes a room, unless a second approver is named: then theirs is needed too."""
    deciders = {d["by"] for d in room["decisions"]}
    if not deciders:
        return False
    second = room.get("second_approver")
    return (second in deciders) if second else True


def outcome(room: dict) -> Optional[dict]:
    """What was decided, when the room is closed and the deciders agree."""
    if room["status"] != "decided" or not room["decisions"]:
        return None
    chosen = {d["option"] for d in room["decisions"]}
    option = next((o for o in room["options"] if o["id"] == room["decisions"][0]["option"]), None)
    return {"option": option, "agreed": len(chosen) == 1, "decisions": len(room["decisions"])}


def waiting_for(room: dict) -> list[str]:
    """Who still has to decide: the second approver when they have not, or anyone while nobody has."""
    if room["status"] != "open":
        return []
    decided = {d["by"] for d in room["decisions"]}
    second = room.get("second_approver")
    if second and second not in decided:
        return [second]
    return [] if decided else ["anyone who may decide"]


def may_decide(room: dict, *, email: str, role: str) -> tuple[bool, Optional[str]]:
    """Mirrors the API so a screen can explain a missing button instead of hiding it."""
    if room["status"] != "open":
        return False, f"This room is {room['status']}."
    if role not in ("admin", "approver"):
        return False, "Decisions are recorded by an Admin or an Approver."
    if any(d["by"] == email for d in room["decisions"]):
        return False, "You have decided; the room keeps your rationale."
    second = room.get("second_approver")
    if second and second != email and len(room["decisions"]) >= 1:
        return False, f"Waiting for the second approver ({second})."
    return True, None


def room_row(room: dict, *, email: str, role: str) -> dict[str, Any]:
    """A room as the lists show it, with what this person may do."""
    can, reason = may_decide(room, email=email, role=role)
    return {
        "id": room["id"], "question": room["question"], "case": room["case"], "zone": room["zone"], "status": room["status"],
        "opened_by": room["opened_by"], "opened_by_kind": room["opened_by_kind"], "created_at": room["created_at"],
        "updated_at": room["updated_at"], "due_at": room["due_at"], "second_approver": room["second_approver"],
        "evidence": len(room["evidence"]), "findings": len(room["findings"]), "decisions": len(room["decisions"]),
        "mine": any(d["by"] == email for d in room["decisions"]), "may_decide": can, "reason": reason,
        "waiting_for": waiting_for(room), "outcome": outcome(room),
    }


def room_view(room: dict, *, email: str, role: str) -> dict[str, Any]:
    return {**room, **room_row(room, email=email, role=role), "evidence": room["evidence"], "findings": room["findings"],
            "decisions": room["decisions"], "counts": {"evidence": len(room["evidence"]), "findings": len(room["findings"]),
                                                       "decisions": len(room["decisions"])}}


def visible(rooms: Iterable[dict], zones: Iterable[str]) -> list[dict]:
    allowed = set(zones)
    return [r for r in rooms if r["zone"] in allowed]
