"""Integrations (build document §9, Slice 3): the MCP servers and model providers the estate actually has.

What exists comes from the instances (the agent lists each profile's MCP servers and connects to them to list
their tools); who may use it comes from the blueprints (an agent's ``mcps`` and ``model``, and the tool
allow- and deny-lists in policies). Server configuration lives on the instance, never in a blueprint.
"""

from __future__ import annotations

from typing import Any, Optional


def _health(rows: list[dict]) -> tuple[str, Optional[str]]:
    """One health for a server across the profiles that have it."""
    probed = [r for r in rows if "ok" in r]
    if not probed:
        return ("disabled", None) if all(r.get("enabled") is False for r in rows) else ("unknown", None)
    bad = next((r for r in probed if not r["ok"]), None)
    if bad:
        return "unreachable", bad.get("error")
    return "healthy", None


def aggregate(*, instances: list[dict], live: dict[str, dict[str, dict]], discovered: dict[str, dict],
              blueprints: list[dict]) -> list[dict]:
    """One row per MCP server and per model provider.

    ``live`` is {instance: {profile: state}} from the last imports, ``discovered`` {instance: {profile: [server]}}
    from the last MCP discovery, ``blueprints`` the parsed latest versions."""
    environment = {i["id"]: i["environment"] for i in instances}
    by_key: dict[tuple[str, str], dict] = {}

    def row(kind: str, name: str) -> dict:
        return by_key.setdefault((kind, name), {
            "kind": kind, "name": name, "instances": [], "profiles": [], "environments": [], "tools": [],
            "models": [], "used_by": [], "allow": {}, "health": "unknown", "error": None, "enabled_everywhere": True,
        })

    # what the instances have
    for instance_id, profiles in (live or {}).items():
        for profile, state in (profiles or {}).items():
            model = state.get("model") or {}
            if model.get("provider"):
                r = row("model", model["provider"])
                if model.get("name") and model["name"] not in r["models"]:
                    r["models"].append(model["name"])
                r["health"] = "healthy"  # a profile runs on it
                for key, value in (("instances", instance_id), ("profiles", f"{instance_id}/{profile}"),
                                   ("environments", environment.get(instance_id, ""))):
                    if value and value not in r[key]:
                        r[key].append(value)
            for name in state.get("mcps") or []:
                r = row("mcp", name)
                for key, value in (("instances", instance_id), ("profiles", f"{instance_id}/{profile}"),
                                   ("environments", environment.get(instance_id, ""))):
                    if value and value not in r[key]:
                        r[key].append(value)

    # what a discovery found (tools, health, enabled)
    found: dict[str, list[dict]] = {}
    for instance_id, servers_by_profile in (discovered or {}).items():
        for profile, servers in (servers_by_profile or {}).items():
            for server in servers or []:
                name = server.get("name")
                if not name:
                    continue
                r = row("mcp", name)
                found.setdefault(name, []).append(server)
                for key, value in (("instances", instance_id), ("profiles", f"{instance_id}/{profile}"),
                                   ("environments", environment.get(instance_id, ""))):
                    if value and value not in r[key]:
                        r[key].append(value)
                for tool in server.get("tools") or []:
                    if tool.get("name") and not any(t["name"] == tool["name"] for t in r["tools"]):
                        r["tools"].append({"name": tool["name"], "description": tool.get("description") or ""})
                if server.get("enabled") is False:
                    r["enabled_everywhere"] = False
                r["transport"] = server.get("transport") or r.get("transport")
                r["endpoint"] = server.get("url") or server.get("command") or r.get("endpoint")
                r["auth"] = server.get("auth") or r.get("auth")
    for name, rows in found.items():
        health, error = _health(rows)
        row("mcp", name).update(health=health, error=error)

    # who may use it, from the blueprints
    for parsed in blueprints or []:
        policies = parsed.get("policies") or []
        for agent in parsed.get("agents") or []:
            for name in agent.get("mcps") or []:
                r = row("mcp", name)
                user = {"agent": agent["id"], "blueprint": parsed["metadata"]["name"]}
                if user not in r["used_by"]:
                    r["used_by"].append(user)
            provider = ((agent.get("model") or {}).get("provider"))
            if provider:
                r = row("model", provider)
                user = {"agent": agent["id"], "blueprint": parsed["metadata"]["name"]}
                if user not in r["used_by"]:
                    r["used_by"].append(user)
                name = (agent.get("model") or {}).get("name")
                if name and name not in r["models"]:
                    r["models"].append(name)
        for policy in policies:
            tools = (policy.get("params") or {}).get("tools") or []
            if not tools or policy.get("kind") not in ("tool-allowlist", "tool-denylist", "external-action-approval"):
                continue
            applies = policy.get("applies_to") or ["*"]
            agents = [a["id"] for a in parsed.get("agents") or []] if "*" in applies else applies
            for tool in tools:
                server = tool.split(".", 1)[0] if "." in tool else None
                if not server or ("mcp", server) not in by_key:
                    continue
                rule = by_key[("mcp", server)]["allow"].setdefault(tool, {"blocked_for": [], "approval_for": []})
                key = "blocked_for" if policy.get("enforcement") == "block" else "approval_for"
                for agent_id in agents:
                    if agent_id not in rule[key]:
                        rule[key].append(agent_id)

    out = sorted(by_key.values(), key=lambda r: (r["kind"] != "mcp", r["name"]))
    for r in out:
        r["instances"] = sorted(r["instances"])
        r["environments"] = sorted({e for e in r["environments"] if e})
        r["profiles"] = sorted(r["profiles"])
        r["models"] = sorted(r["models"])
    return out


def mcp_config(name: str, *, url: Optional[str] = None, command: Optional[str] = None, args: Optional[list[str]] = None,
               auth: Optional[str] = None) -> dict[str, Any]:
    """The body the agent sends to Hermes to add a server. Credentials are never part of it: they are added on
    the instance, so no secret passes through (or is stored by) Fleet Control."""
    if bool(url) == bool(command):
        raise ValueError("give either a url (remote server) or a command (stdio server), not both")
    config: dict[str, Any] = {"name": name}
    if url:
        config["url"] = url
    else:
        config["command"] = command
        config["args"] = args or []
    if auth:
        config["auth"] = auth
    return config
