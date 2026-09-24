"""Effective access (build document §7, Slice 4): what a person may do, intersected with what an agent may do,
intersected with what the connected system allows, each line with the rule that decides it.

Nothing here is a second source of truth. Every answer is read from the rules the API enforces: the role
permissions (auth.PERMISSIONS), content zones (content.may_read), the agent's grants in its applied blueprint
(content_zones, mcps, toolsets) and the blueprint's policies, and the Decision Room rules (rooms.may_decide).
A connected system's own permissions are not visible to Fleet Control, so they are shown as decided by that
system, never guessed.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .auth import PERMISSIONS, ROLES, role_label

# What each permission lets a person do, grouped the way the portal is.
PERMISSION_LABEL: dict[str, tuple[str, str]] = {
    "instances.read": ("Estate", "See instances and their state"),
    "instances.connect": ("Estate", "Connect, edit and forget instances"),
    "instances.operate": ("Estate", "Import live profiles, run drift scans and discoveries, change MCP servers"),
    "blueprints.read": ("Design", "Read blueprints"),
    "blueprints.write": ("Design", "Edit blueprints and drafts, and use the Fleet Architect"),
    "plans.read": ("Plans", "Read plans"),
    "plans.create": ("Plans", "Create plans from a blueprint"),
    "plans.approve.nonprod": ("Plans", "Approve lab and staging plans"),
    "plans.approve.production": ("Plans", "Approve production plans (never your own)"),
    "plans.apply.nonprod": ("Plans", "Apply to lab and staging"),
    "plans.apply.production": ("Plans", "Apply approved production plans"),
    "drift.read": ("Estate", "See drift"),
    "drift.resolve": ("Estate", "Resolve drift"),
    "audit.read": ("Govern", "Read the audit log"),
    "users.read": ("Govern", "See people and access"),
    "users.manage": ("Govern", "Invite people, change roles, revoke anyone's API tokens"),
    "rooms.read": ("Workspace", "Read Decision Rooms in zones they may read"),
    "rooms.open": ("Workspace", "Open Decision Rooms and add evidence"),
    "rooms.decide": ("Workspace", "Decide in Decision Rooms"),
    "content.read": ("Workspace", "Read content and fleet outputs in zones they may read"),
    "content.manage": ("Workspace", "Create zones, upload files, file outputs"),
    "tests.run": ("Operate", "Run and stop tests in the Test Lab"),
    "tests.manage": ("Operate", "Delete test runs"),
    "assurance.read": ("Operate", "Read assurance claims"),
    "settings.read": ("Settings", "Read workspace settings"),
    "settings.manage": ("Settings", "Change workspace settings"),
    "messaging.read": ("Estate", "See messaging channels, rules and what was sent"),
    "messaging.manage": ("Estate", "Add channels, send test messages, switch webhooks on"),
    "ask.use": ("Workspace", "Ask the fleet"),
}


def _roles_with(permission: str) -> str:
    roles = [role_label(r) for r in ROLES if r in PERMISSIONS.get(permission, ())]
    return ", ".join(roles) or "no role"


def person_permissions(role: str) -> list[dict]:
    """Every permission, whether this role has it, and why."""
    out = []
    for perm in PERMISSIONS:
        area, label = PERMISSION_LABEL.get(perm, ("Other", perm))
        ok = role in PERMISSIONS[perm]
        out.append({"permission": perm, "area": area, "label": label, "allowed": ok,
                    "why": f"the {role_label(role)} role has it" if ok else f"only {_roles_with(perm)}"})
    return out


def person_zones(role: str, zones: Iterable[dict]) -> list[dict]:
    out = []
    for z in zones:
        readers = z.get("read_roles") or []
        if role == "admin":
            ok, why = True, "an Admin reads every zone"
        elif role in readers:
            ok, why = True, f"{role_label(role)} is one of the zone's readers"
        else:
            ok, why = False, "readers: " + (", ".join(role_label(r) for r in readers) if readers else "Admins only")
        out.append({"zone": z["id"], "name": z.get("name") or z["id"], "classification": z.get("classification"),
                    "allowed": ok, "why": why})
    return out


def agent_grants(parsed: dict, agent_id: str) -> Optional[dict]:
    """An agent as its (applied) blueprint describes it: what it may reach and which policies hold it back."""
    agent = next((a for a in parsed.get("agents") or [] if a["id"] == agent_id), None)
    if not agent:
        return None
    blocked, gated = [], []
    for p in parsed.get("policies") or []:
        applies = p.get("applies_to") or ["*"]
        if "*" not in applies and agent_id not in applies:
            continue
        tools = (p.get("params") or {}).get("tools") or []
        target = blocked if p.get("enforcement") == "block" else gated if p.get("enforcement") == "approve" else None
        if target is not None:
            target += [{"tool": t, "policy": p["id"]} for t in tools]
    return {"blueprint": parsed["metadata"]["name"], "version": parsed["metadata"].get("version"), "agent": agent_id,
            "profile": agent.get("hermes_profile") or agent_id, "model": agent.get("model"),
            "content_zones": list(agent.get("content_zones") or []), "mcps": list(agent.get("mcps") or []),
            "toolsets": list(agent.get("toolsets") or []), "skills": list(agent.get("skills") or []),
            "blocked": blocked, "approval": gated}


def tool_verdict(grants: dict, tool: str, *, servers: Optional[dict] = None) -> dict:
    """May this agent call ``tool`` ("server.tool", "server" or a Hermes tool like "web.fetch"), and why.

    ``servers`` is {server: {"auth": …, "health": …}} from Integrations, for the system's own part."""
    from .testlab import tool_matches

    tool = tool.strip()
    name, _, rest = tool.partition(".")
    policy = next((b for b in grants["blocked"] if tool_matches(b["tool"], tool) or b["tool"] == tool), None)
    if policy:
        return {"allowed": False, "layer": "policy", "why": f"blocked by policy {policy['policy']} in {grants['blueprint']}"}
    servers = servers or {}
    if name in grants["mcps"] or name in servers:
        if name not in grants["mcps"]:
            return {"allowed": False, "layer": "agent", "why": f"{name} is not among the agent's MCP servers (mcps) in {grants['blueprint']}"}
        gate = next((g for g in grants["approval"] if tool_matches(g["tool"], tool) or g["tool"] == tool), None)
        system = servers.get(name) or {}
        auth = system.get("auth")
        sys_why = (f"{name} checks its own permissions: the profile signs in with OAuth, as itself" if auth == "oauth"
                   else f"{name} checks its own permissions" if auth else f"{name}'s own permissions are not visible to Fleet Control")
        return {"allowed": True, "layer": "system", "needs_approval": bool(gate),
                "why": f"{name} is one of the agent's MCP servers" + (f"; each call waits for a person (policy {gate['policy']})" if gate else ""),
                "system": sys_why, "system_health": system.get("health")}
    if name in grants["toolsets"]:
        gate = next((g for g in grants["approval"] if tool_matches(g["tool"], tool) or g["tool"] == tool), None)
        return {"allowed": True, "layer": "agent", "needs_approval": bool(gate),
                "why": f"the {name} toolset is on for the agent" + (f"; each call waits for a person (policy {gate['policy']})" if gate else "")}
    return {"allowed": False, "layer": "agent",
            "why": f"neither an MCP server nor a toolset of the agent provides {tool}" + (f" (toolsets: {', '.join(grants['toolsets'])})" if grants["toolsets"] else " (it has no toolsets)")}


def combined_zones(person: list[dict], grants: Optional[dict]) -> list[dict]:
    """Content that reaches a person through the agent (Ask the fleet, rooms it files into): only zones both may read."""
    if grants is None:
        return []
    agent_zones = set(grants["content_zones"])
    out = []
    for z in person:
        if z["allowed"] and z["zone"] in agent_zones:
            out.append({**z, "why": "both may read it"})
        elif z["zone"] in agent_zones:
            out.append({**z, "allowed": False, "why": f"the agent may read it; the person may not ({z['why']})"})
        elif z["allowed"]:
            out.append({**z, "allowed": False, "why": f"the person may read it; the agent is not granted it in {grants['blueprint']}"})
    return out


def why_room(room: dict, *, email: str, role: str, zones: Iterable[dict]) -> dict:
    """May this person decide in this room? The same rules the API applies, in order."""
    from .rooms import may_decide

    zone = next((z for z in zones if z["id"] == room["zone"]), None)
    if zone is None or not (role == "admin" or role in (zone.get("read_roles") or [])):
        return {"allowed": False, "why": f"they cannot read zone {room['zone']}, so they cannot see the room at all"}
    ok, reason = may_decide(room, email=email, role=role)
    return {"allowed": ok, "why": "they may decide now" if ok else reason.rstrip(".")}


def why_apply(environment: str, role: str) -> dict:
    perm = "plans.apply.production" if environment == "production" else "plans.apply.nonprod"
    approve = "plans.approve.production" if environment == "production" else "plans.approve.nonprod"
    can_apply, can_approve = role in PERMISSIONS[perm], role in PERMISSIONS[approve]
    lines = [f"apply: {'yes' if can_apply else 'no'} ({'role has ' + perm if can_apply else 'only ' + _roles_with(perm)})",
             f"approve: {'yes' if can_approve else 'no'} ({'role has ' + approve if can_approve else 'only ' + _roles_with(approve)})"]
    if environment == "production":
        lines.append("a production plan also needs its approvals (Settings → Approvals) from people other than its creator, "
                     "and its agent tests passed on lab or staging")
    return {"allowed": can_apply, "why": "; ".join(lines)}


def summary(permissions: list[dict], zones: list[dict]) -> dict[str, Any]:
    return {"permissions": sum(p["allowed"] for p in permissions), "of_permissions": len(permissions),
            "zones": sum(z["allowed"] for z in zones), "of_zones": len(zones)}
