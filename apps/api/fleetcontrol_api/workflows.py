"""Workflow runs (build document §9, Slice 5): executing the steps a blueprint declares.

A workflow is an ordered list of steps. A step is one agent producing an artifact, a parallel
block of agents, a human gate that waits for a role to approve, or a Decision Room opened with
what the run has gathered. This module is the state machine only — no I/O, no jobs, no store.
The API enqueues each agent step as a ``hermes_run`` job (the machinery the Architect and Test
Lab already use) and feeds the results back here.

The run records what actually happened, step by step, so a finished run reads as evidence:
which agent produced which artifact, who approved, and what the room was asked.
"""

from __future__ import annotations

import re
import time
from typing import Any, Iterable, Optional

STEP_KINDS = ("agent", "parallel", "human_gate", "decision_room")
STEP_STATUSES = ("pending", "running", "waiting", "done", "failed", "skipped")
RUN_STATUSES = ("running", "waiting", "done", "failed", "cancelled")
_DURATION = re.compile(r"^(\d+)(m|h|d)$")
_SECONDS = {"m": 60, "h": 3600, "d": 86400}


class WorkflowError(ValueError):
    pass


def duration_seconds(text: str) -> int:
    m = _DURATION.match(str(text or "").strip())
    if not m:
        raise WorkflowError("a timeout looks like 30m, 24h or 2d")
    return int(m.group(1)) * _SECONDS[m.group(2)]


def _member(raw: dict) -> dict:
    if not raw.get("agent"):
        raise WorkflowError("a step names the agent that runs it")
    return {"agent": raw["agent"], "artifact": raw.get("artifact"), "input": raw.get("input")}


def normalize_step(raw: dict, index: int) -> dict:
    """One declared step, as the runtime and the screen both read it."""
    base = {"index": index, "status": "pending", "started_at": None, "finished_at": None, "error": None}
    if "parallel" in raw:
        members = [_member(m) for m in raw["parallel"]]
        if len(members) < 2:
            raise WorkflowError("a parallel block runs two or more agents")
        return {**base, "kind": "parallel", "members": members, "jobs": {}, "results": {},
                "label": " · ".join(m["agent"] for m in members)}
    if "human_gate" in raw:
        role = raw["human_gate"]
        return {**base, "kind": "human_gate", "role": role, "timeout": raw.get("timeout", "24h"),
                "escalate_to": raw.get("escalate_to"), "on_reject": raw.get("on_reject"),
                "gate": None, "escalated_at": None, "label": "Approval by " + str(role)}
    if raw.get("open_decision_room"):
        return {**base, "kind": "decision_room", "question_template": raw.get("question_template"),
                "room_id": None, "label": "Open a Decision Room"}
    member = _member(raw)
    return {**base, "kind": "agent", "members": [member], "jobs": {}, "results": {}, "label": member["agent"]}


def normalize_steps(steps: Iterable[dict]) -> list[dict]:
    out = [normalize_step(s, i) for i, s in enumerate(steps)]
    if not out:
        raise WorkflowError("a workflow has at least one step")
    return out


def agents_used(steps: Iterable[dict]) -> list[str]:
    """Every agent the workflow runs, in order, without repeats."""
    seen: list[str] = []
    for s in steps:
        for m in s.get("members", []):
            if m["agent"] not in seen:
                seen.append(m["agent"])
    return seen


def missing_requirements(steps: Iterable[dict], agents: dict[str, dict], available_mcps: Iterable[str]) -> list[str]:
    """Why this instance cannot run this workflow: an agent the blueprint no longer has, or an MCP
    server the instance does not. A run that cannot reach SAS must say so, never improvise."""
    have = set(available_mcps)
    problems: list[str] = []
    for agent_id in agents_used(steps):
        agent = agents.get(agent_id)
        if not agent:
            problems.append(agent_id + " is not an agent in this blueprint")
            continue
        for mcp in agent.get("mcps", []):
            if mcp not in have:
                problems.append(f"{agent_id} needs the MCP server {mcp!r}, which this instance does not have")
    return problems


EXECUTORS = ("runs", "kanban")  # one Hermes run per agent step, or linked tasks on the instance's Kanban board


def new_run(*, run_id: str, blueprint: str, version: int, workflow_id: str, instance_id: str, steps: list[dict],
            started_by: str, at: float, zone: Optional[str] = None, case: Optional[str] = None,
            input_text: Optional[str] = None, executor: str = "runs") -> dict:
    if executor not in EXECUTORS:
        raise WorkflowError(f"a run executes as one of: {', '.join(EXECUTORS)}")
    return {
        "id": run_id, "blueprint": blueprint, "version": version, "workflow_id": workflow_id, "executor": executor,
        "board": "fleetcontrol" if executor == "kanban" else None, "sync_job": None,
        "instance_id": instance_id, "zone": zone, "case": case, "input": (input_text or "").strip() or None,
        "status": "running", "started_by": started_by, "started_at": at, "updated_at": at, "finished_at": None,
        "steps": normalize_steps(steps), "artifacts": {}, "room_id": None, "error": None,
    }


def segment(run: dict, start: int) -> list[dict]:
    """The agent steps from ``start`` up to the next human gate or room: what Kanban can run on its own, in order,
    before a person has to look."""
    out = []
    for s in run["steps"][start:]:
        if s["kind"] not in ("agent", "parallel"):
            break
        out.append(s)
    return out


def kanban_tasks(run: dict, steps: list[dict], first_input: dict[str, str], instructions_for) -> list[dict]:
    """Kanban tasks for a segment: one per agent, parented on the previous step's tasks (so a parallel group
    shares its parents and the next step waits for all of it). The first step's tasks carry the whole context;
    later ones read their parents' results through Kanban."""
    tasks, previous = [], []
    for s in steps:
        keys = []
        for m in s["members"]:
            key = f"{s['index']}:{m['agent']}:{s.get('attempt', 0)}"
            body = first_input.get(m["agent"]) if s is steps[0] else "\n\n".join(filter(None, [
                ("Request: " + run["input"]) if run.get("input") else None,
                ("Input: " + m["input"]) if m.get("input") else None,
                "The results of the steps before yours are in your task's context (parent results).",
                ("Produce: " + m["artifact"]) if m.get("artifact") else None]))
            tasks.append({"key": key, "step": s["index"], "agent": m["agent"],
                          "title": f"{run['workflow_id']} · step {s['index'] + 1} · {m['agent']}" + (f" · {run['case']}" if run.get("case") else ""),
                          "body": body + "\n\n" + instructions_for(m, s), "parents": list(previous), "tenant": run["id"],
                          "idempotency_key": f"{run['id']}:{key}"})
            keys.append(key)
        previous = keys
    return tasks


def kanban_outcome(task: dict) -> Optional[tuple[bool, Optional[str], Optional[str]]]:
    """(ok, output, error) once a Kanban task has settled; None while it is still in play."""
    status = task.get("status")
    if status == "done":
        output = task.get("summary") or task.get("result")
        return (True, output, None) if output else (False, None, "the task finished without a result")
    if status in ("blocked", "archived", "missing"):
        last = next((r for r in reversed(task.get("runs") or []) if r.get("error") or r.get("summary")), {})
        return False, None, task.get("error") or last.get("error") or f"the task is {status}" + (f": {last['summary']}" if last.get("summary") else "")
    return None


def current_step(run: dict) -> Optional[dict]:
    """The step the run is on: the first that has not finished."""
    return next((s for s in run["steps"] if s["status"] in ("pending", "running", "waiting")), None)


def start_step(step: dict, at: float) -> dict:
    step["status"] = "waiting" if step["kind"] == "human_gate" else "running"
    step["started_at"] = at
    return step


def pending_members(step: dict) -> list[dict]:
    """Agents in this step with no result yet — what the API still has to enqueue."""
    return [m for m in step.get("members", []) if m["agent"] not in step.get("results", {})]


def record_result(run: dict, index: int, agent: str, *, ok: bool, output: Optional[str] = None,
                  error: Optional[str] = None, artifact_id: Optional[str] = None, run_ref: Optional[str] = None,
                  at: Optional[float] = None) -> dict:
    """One agent step came back. The step finishes when every member has answered."""
    at = at if at is not None else time.time()
    step = run["steps"][index]
    if step["kind"] not in ("agent", "parallel"):
        raise WorkflowError("that step is not an agent step")
    step["results"][agent] = {"ok": ok, "output": output, "error": error, "artifact_id": artifact_id,
                              "run_ref": run_ref, "at": at}
    member = next((m for m in step["members"] if m["agent"] == agent), None)
    if ok and member and member.get("artifact"):
        run["artifacts"][member["artifact"]] = {"agent": agent, "step": index, "artifact_id": artifact_id, "at": at}
    if not pending_members(step):
        failed = sorted(a for a, r in step["results"].items() if not r["ok"])
        step["status"] = "failed" if failed else "done"
        step["finished_at"] = at
        if failed:
            step["error"] = ", ".join(failed) + " did not finish"
            run["status"], run["finished_at"] = "failed", at
            run["error"] = step["error"]
    run["updated_at"] = at
    return run


def _last_step_with(run: dict, agent: Optional[str], before: int) -> Optional[int]:
    """The last step before ``before`` that runs ``agent``: where a gate sends work back to."""
    if not agent:
        return None
    for s in reversed(run["steps"][:before]):
        if any(m["agent"] == agent for m in s.get("members", [])):
            return s["index"]
    return None


def _reset_step(step: dict) -> None:
    step.update(status="pending", started_at=None, finished_at=None, error=None)
    if step["kind"] in ("agent", "parallel"):
        # what it produced last time stays with the step, so the next attempt revises it instead of starting over
        kept = {a: r["output"] for a, r in (step.get("results") or {}).items() if r.get("ok") and r.get("output")}
        if kept:
            step["previous"] = kept
        step["jobs"], step["results"], step["tasks"] = {}, {}, {}
        step["attempt"] = step.get("attempt", 0) + 1  # a fresh attempt: Kanban gets new tasks, never the old ones back
    if step["kind"] == "human_gate":
        step["gate"], step["escalated_at"] = None, None  # a reopened gate gets its full timeout again
    if step["kind"] == "decision_room":
        step["room_id"] = None


def may_decide_gate(step: dict, *, email: str, role: str) -> tuple[bool, Optional[str]]:
    """Who may decide a gate: its role, an Admin, and once it is overdue and escalated, whoever it escalated to
    (a role or one person's email)."""
    if step["kind"] != "human_gate":
        return False, "that step is not a human gate"
    if step["status"] not in ("waiting", "running"):
        return False, "that gate is not open"
    if role in ("admin", step["role"]):
        return True, None
    target = step.get("escalate_to")
    if step.get("escalated_at") and target and target in (role, email):
        return True, None
    return False, f"this gate is for the {step['role']} role" + (f" (after {step['timeout']}, also {target})" if target else "")


def decide_gate(run: dict, index: int, *, by: str, role: str, approve: bool, note: str = "",
                at: Optional[float] = None) -> dict:
    """A human gate: the named role approves, or sends the work back to an earlier agent."""
    at = at if at is not None else time.time()
    step = run["steps"][index]
    ok, why = may_decide_gate(step, email=by, role=role)
    if not ok:
        raise WorkflowError(why)
    step["gate"] = {"by": by, "approved": bool(approve), "note": (note or "").strip(), "at": at}
    step["finished_at"] = at
    run["updated_at"] = at
    if approve:
        step["status"] = "done"
        return run
    step["status"] = "failed"
    step["error"] = "sent back by " + by
    target = _last_step_with(run, step.get("on_reject"), index)
    if target is None:
        run["status"], run["finished_at"] = "failed", at
        run["error"] = "rejected at step " + str(index + 1)
        return run
    # Loop back: the named agent runs again with the reviewer's note, and the steps after it follow.
    for s in run["steps"][target:]:
        _reset_step(s)
    run["status"] = "running"
    run["steps"][target]["note"] = (note or "").strip() or None
    return run


def finish_room_step(run: dict, index: int, room_id: str, at: Optional[float] = None) -> dict:
    at = at if at is not None else time.time()
    step = run["steps"][index]
    if step["kind"] != "decision_room":
        raise WorkflowError("that step does not open a room")
    step.update(status="done", room_id=room_id, finished_at=at)
    run["room_id"], run["updated_at"] = room_id, at
    return run


def settle(run: dict, at: Optional[float] = None) -> dict:
    """Close the run once every step has finished."""
    at = at if at is not None else time.time()
    if run["status"] in ("failed", "cancelled", "done"):
        return run
    if current_step(run) is None:
        run.update(status="done", finished_at=at, updated_at=at)
    return run


def gate_overdue(step: dict, now: Optional[float] = None) -> bool:
    """A gate past its timeout."""
    now = now if now is not None else time.time()
    if step["kind"] != "human_gate" or step["status"] != "waiting" or step.get("started_at") is None:
        return False
    return now - step["started_at"] > duration_seconds(step.get("timeout", "24h"))


def escalate_due(run: dict, now: Optional[float] = None) -> list[dict]:
    """Mark every open gate past its timeout as escalated, once; returns the ones escalated now. From then on its
    ``escalate_to`` may decide it too (may_decide_gate), and the API tells them."""
    now = now if now is not None else time.time()
    out = []
    for step in run["steps"]:
        if gate_overdue(step, now) and not step.get("escalated_at"):
            step["escalated_at"] = now
            out.append(step)
    if out:
        run["updated_at"] = now
    return out


ARTIFACT_CHARS = 6_000  # how much of each earlier artifact the next agent reads


def compose_input(run: dict, member: dict, step: dict) -> str:
    """What the agent is asked: the run's request, its declared input, what earlier steps produced (their text,
    so a reviewer can review it), and a reviewer's note when the work was sent back."""
    parts: list[str] = []
    if run.get("input"):
        parts.append("Request: " + run["input"])
    if member.get("input"):
        parts.append("Input: " + str(member["input"]))
    for s in run["steps"][: step["index"]]:
        for agent, result in (s.get("results") or {}).items():
            if result.get("ok") and result.get("output"):
                name = next((m.get("artifact") for m in s.get("members", []) if m["agent"] == agent), None) or f"{agent}'s result"
                text = result["output"]
                cut = "" if len(text) <= ARTIFACT_CHARS else f"\n[… {len(text) - ARTIFACT_CHARS} more characters not shown]"
                parts.append(f"Earlier in this run, {agent} produced {name}:\n{text[:ARTIFACT_CHARS]}{cut}")
    previous = (step.get("previous") or {}).get(member["agent"])
    if previous:
        cut = "" if len(previous) <= ARTIFACT_CHARS else f"\n[… {len(previous) - ARTIFACT_CHARS} more characters not shown]"
        parts.append(f"Your previous version, which was sent back:\n{previous[:ARTIFACT_CHARS]}{cut}")
    if step.get("note"):
        parts.append("A reviewer sent this back to you with this note: " + step["note"])
    if member.get("artifact"):
        parts.append("Produce: " + str(member["artifact"]))
    return "\n\n".join(parts)


def instructions(run: dict, member: dict, step: dict) -> str:
    """The run's instructions for one agent step."""
    artifact = member.get("artifact")
    return (f"You are step {step['index'] + 1} of {len(run['steps'])} of the workflow {run['workflow_id']} "
            f"({run['blueprint']} v{run['version']}), run by Fleet Control. A later step or a person reads what you produce "
            "before anything is decided.\n"
            + (f"Reply with the {artifact} itself, complete, as your whole answer." if artifact else "Reply with your result as your whole answer.")
            + "\nUse only what you are given and what your tools return; say plainly what you could not establish.")


def question_for(run: dict, template: Optional[str], values: Optional[dict] = None) -> str:
    """The Decision Room's question. Unknown placeholders stay visible rather than being guessed."""
    text = (template or ("Approve the outcome of " + run["workflow_id"] + "?")).strip()
    for key, value in (values or {}).items():
        text = text.replace("{" + key + "}", str(value))
    return text


def progress(run: dict) -> dict[str, Any]:
    steps = run["steps"]
    done = sum(1 for s in steps if s["status"] in ("done", "skipped"))
    cur = current_step(run)
    return {"done": done, "total": len(steps), "current": cur["index"] if cur else None,
            "current_label": cur["label"] if cur else None}


def run_row(run: dict, now: Optional[float] = None) -> dict[str, Any]:
    """A run as the list shows it."""
    cur = current_step(run)
    return {"id": run["id"], "blueprint": run["blueprint"], "version": run["version"],
            "workflow_id": run["workflow_id"], "instance_id": run["instance_id"], "status": run["status"],
            "case": run.get("case"), "started_by": run["started_by"], "started_at": run["started_at"],
            "updated_at": run["updated_at"], "finished_at": run.get("finished_at"), "error": run.get("error"),
            "room_id": run.get("room_id"), "progress": progress(run),
            "awaiting_role": cur["role"] if cur and cur["kind"] == "human_gate" else None,
            "overdue": any(gate_overdue(s, now) for s in run["steps"])}
