"""Plan computation: Blueprint desired state vs an instance's live state -> ordered change rows.

Pure functions, no I/O, so the plan shown on the ApplyPlan screen is exactly what the agent
executes. Output shape mirrors the screen: symbol, object, description, method, risk.
"""

from __future__ import annotations

from typing import Any

from fleetcontrol_blueprint import Blueprint

SYMBOL = {"create": "+", "update": "~", "remove": "-", "approval": "!"}


def _row(kind: str, obj: str, description: str, method: str, risk: str = "low", ops: list[dict] | None = None) -> dict:
    return {"kind": kind, "symbol": SYMBOL[kind], "object": obj, "description": description, "method": method, "risk": risk, "ops": ops or []}


DEFAULT_APPROVALS = {"production": 2, "staging": 0, "lab": 0}  # build document §7; Settings → Approvals changes them


def compute_plan(bp: Blueprint, live: dict[str, dict], *, target_instance: str, agent_installed: bool, environment: str,
                 default_approvals: dict[str, int] | None = None) -> dict:
    """``live`` is {profile_name: live_profile_state} from the agent's import/drift jobs.
    Profiles present live but not in the blueprint are reported as unmanaged, never removed."""
    method = "Agent" if agent_installed else "API (read-only)"
    rows: list[dict] = []
    desired = bp.managed_fields()

    for a in bp.agents:
        name = a.profile_name
        want = desired[name]
        have = live.get(name)
        if have is None:
            ops = [
                {"op": "ensure_profile", "profile": name, "description": a.role, "provider": a.model.provider, "model": a.model.name},
                {"op": "write_soul", "profile": name, "content": a.soul.render()},
                # Hermes starts a new profile with its default skills and toolsets on (18 toolsets on 0.21.2), so
                # enabling the blueprint's alone leaves drift; sync makes the live lists exactly the blueprint's.
                {"op": "sync_skills", "profile": name, "skills": list(a.skills)},
                {"op": "sync_toolsets", "profile": name, "toolsets": list(a.toolsets)},
            ]
            rows.append(_row("create", f"profile {name}", f"Role, model {a.model.name}, {len(a.skills)} skills, {len(a.mcps)} MCPs", method, "medium", ops))
            continue
        ops = []
        parts = []
        if want["model"] != have.get("model"):
            ops.append({"op": "ensure_profile", "profile": name, "description": a.role, "provider": a.model.provider, "model": a.model.name})
            parts.append(f"model: {(have.get('model') or {}).get('name')} → {a.model.name}")
        elif want["description"] != have.get("description"):
            ops.append({"op": "ensure_profile", "profile": name, "description": a.role, "provider": a.model.provider, "model": a.model.name})
            parts.append("description")
        if want["soul_sha256"] != have.get("soul_sha256"):
            ops.append({"op": "write_soul", "profile": name, "content": a.soul.render()})
            parts.append("SOUL")
        add = sorted(set(want["skills"]) - set(have.get("skills") or []))
        rem = sorted(set(have.get("skills") or []) - set(want["skills"]))
        for s in add:
            ops.append({"op": "set_skill", "profile": name, "skill": s, "enabled": True})
        for s in rem:
            ops.append({"op": "set_skill", "profile": name, "skill": s, "enabled": False})
        if add or rem:
            parts.append("skills: " + ", ".join([f"+{s}" for s in add] + [f"−{s}" for s in rem]))
        tadd = sorted(set(want["toolsets"]) - set(have.get("toolsets") or []))
        trem = sorted(set(have.get("toolsets") or []) - set(want["toolsets"]))
        for t in tadd:
            ops.append({"op": "set_toolset", "profile": name, "toolset": t, "enabled": True})
        for t in trem:
            ops.append({"op": "set_toolset", "profile": name, "toolset": t, "enabled": False})
        if tadd or trem:
            parts.append("toolsets: " + ", ".join([f"+{t}" for t in tadd] + [f"−{t}" for t in trem]))
        if set(want["mcps"]) != set(have.get("mcps") or []):
            parts.append("MCP servers differ (manual step in Slice 1)")
        if parts:
            rows.append(_row("update", f"profile {name}", "; ".join(parts), method, "low", ops))

    unmanaged = sorted(set(live) - set(desired))

    # approvals before apply: the organisation's floor for this environment (Settings → Approvals); a blueprint
    # target may ask for more, never fewer
    floor = (default_approvals or DEFAULT_APPROVALS).get(environment, 0)
    target = next((t.requires_approvals for t in bp.targets if t.instance == target_instance), 0)
    approvals_required = max(floor, target)
    for p in bp.policies:
        if p.enforcement == "approve":
            rows.append(_row("approval", f"policy {p.id}", p.description + " — enforced by the agent's pre_tool_call hook", "Agent" if agent_installed else "not enforced (no agent)", "high"))

    # policy file the agent pushes per profile
    policy_push = {}
    for a in bp.agents:
        deny, approve = set(), set()
        for p in bp.policies:
            if "*" in p.applies_to or a.id in p.applies_to:
                tools = set((p.params or {}).get("tools") or [])
                if p.enforcement == "block":
                    deny |= tools
                elif p.enforcement == "approve":
                    approve |= tools
        policy_push[a.profile_name] = {"version": 1, "profile": a.profile_name, "deny_tools": sorted(deny), "approve_tools": sorted(approve), "rules": []}

    blocked = not agent_installed and any(r["kind"] in ("create", "update") for r in rows)
    return {
        "target_instance": target_instance,
        "environment": environment,
        "blueprint": {"name": bp.metadata.name, "version": bp.metadata.version},
        "changes": rows,
        "no_change": {"profiles": [a.profile_name for a in bp.agents if not any(r["object"] == f"profile {a.profile_name}" for r in rows)]},
        "unmanaged_profiles": unmanaged,
        "approvals_required": approvals_required,
        "policy_push": policy_push,
        "can_apply": not blocked,
        "blocked_reason": "This instance is connected API-only; install the Fleet Control Agent to apply changes." if blocked else None,
    }


def to_agent_job(plan: dict) -> list[dict]:
    """Jobs for the daemon, in order: push policy, then apply changes with a snapshot."""
    ops = [op for r in plan["changes"] for op in r["ops"]]
    return [
        {"kind": "push_policy", "params": {"profiles": plan["policy_push"]}},
        {"kind": "apply", "params": {"changes": ops, "snapshot": True}},
    ]
