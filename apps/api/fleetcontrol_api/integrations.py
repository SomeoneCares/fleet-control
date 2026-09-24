"""Integrations (build document §9, Slice 3): the MCP servers and model providers the estate actually has.

What exists comes from the instances (the agent lists each profile's MCP servers and connects to them to list
their tools); who may use it comes from the blueprints (an agent's ``mcps`` and ``model``, and the tool
allow- and deny-lists in policies). Server configuration lives on the instance, never in a blueprint.
"""

from __future__ import annotations

from typing import Any, Optional


def _entry_health(server: dict) -> str:
    if "ok" in server:
        return "healthy" if server["ok"] else "unreachable"
    return "disabled" if server.get("enabled") is False else "unknown"


# A failed probe with no error text is almost always a server that did not answer, not a credential problem.
NO_ANSWER = "no error text: the server did not answer (check that the server itself is up before the credentials)"


def _health(entries: list[dict]) -> tuple[str, Optional[str]]:
    """One health for a server from its per-profile healths. Each profile has its own registration and its own
    tokens, so one bad profile does not make the server unreachable: that is "degraded", and says which."""
    probed = [e for e in entries if e["health"] in ("healthy", "unreachable")]
    if not probed:
        return ("disabled", None) if entries and all(e["health"] == "disabled" for e in entries) else ("unknown", None)
    bad = [e for e in probed if e["health"] == "unreachable"]
    if not bad:
        return "healthy", None
    first = bad[0]["error"] or NO_ANSWER
    if len(bad) == len(probed):
        return "unreachable", first if len(bad) == 1 else f"unreachable from all {len(bad)} profiles; first: {first}"
    where = ", ".join(e["profile"] for e in bad)
    return "degraded", f"{len(bad)} of {len(probed)} profiles cannot reach it ({where}): {first}"


def aggregate(*, instances: list[dict], live: dict[str, dict[str, dict]], discovered: dict[str, dict],
              blueprints: list[dict], drafts: Optional[list[dict]] = None, live_at: Optional[dict[str, float]] = None,
              discovered_at: Optional[dict[str, dict[str, float]]] = None) -> list[dict]:
    """One row per MCP server and per model provider.

    ``live`` is {instance: {profile: state}} from the last imports, ``discovered`` {instance: {profile: [server]}}
    from the last MCP discovery of each profile. ``live_at`` {instance: when imported} and ``discovered_at``
    {instance: {profile: when discovered}} decide which of the two is newer: a profile's MCP servers are the ones
    the newer source lists, so a server deregistered since the import stops showing under that profile (and one
    removed since the discovery stops too). Without times, the discovery wins where there is one.

    ``blueprints`` are the parsed newest *applied* version of each blueprint: they say who uses what (``used_by``)
    and which policies hold. ``drafts`` are newer unapplied versions: they only show as ``planned_by``."""
    environment = {i["id"]: i["environment"] for i in instances}
    live_at, discovered_at = live_at or {}, discovered_at or {}
    by_key: dict[tuple[str, str], dict] = {}
    entries: dict[str, list[dict]] = {}

    def row(kind: str, name: str) -> dict:
        return by_key.setdefault((kind, name), {
            "kind": kind, "name": name, "instances": [], "profiles": [], "environments": [], "tools": [],
            "models": [], "used_by": [], "planned_by": [], "allow": {}, "health": "unknown", "error": None,
            "enabled_everywhere": True, "profile_health": [],
        })

    def place(r: dict, instance_id: str, profile: str) -> None:
        for key, value in (("instances", instance_id), ("profiles", f"{instance_id}/{profile}"),
                           ("environments", environment.get(instance_id, ""))):
            if value and value not in r[key]:
                r[key].append(value)

    # what the instances have: per profile, from the newer of the import and the discovery
    for instance_id in sorted(set(live or {}) | set(discovered or {})):
        profiles = (live or {}).get(instance_id) or {}
        found = (discovered or {}).get(instance_id) or {}
        for profile in sorted(set(profiles) | set(found)):
            state = profiles.get(profile)
            model = (state or {}).get("model") or {}
            if model.get("provider"):
                r = row("model", model["provider"])
                if model.get("name") and model["name"] not in r["models"]:
                    r["models"].append(model["name"])
                r["health"] = "healthy"  # a profile runs on it
                place(r, instance_id, profile)

            servers = {s["name"]: s for s in found.get(profile) or [] if s.get("name")}
            d_at, l_at = discovered_at.get(instance_id, {}).get(profile), live_at.get(instance_id)
            if profile in found and (d_at is None or l_at is None or d_at >= l_at):
                names = sorted(servers)
            elif state is not None:
                names = sorted(state.get("mcps") or [])
            else:
                continue  # discovered once, but a newer import no longer has the profile
            for name in names:
                r = row("mcp", name)
                place(r, instance_id, profile)
                server = servers.get(name)
                entry = {"instance": instance_id, "profile": profile, "health": "unknown", "error": None}
                entries.setdefault(name, []).append(entry)
                if server is None:
                    continue
                entry.update(health=_entry_health(server), error=server.get("error") or None)
                for tool in server.get("tools") or []:
                    if tool.get("name") and not any(t["name"] == tool["name"] for t in r["tools"]):
                        r["tools"].append({"name": tool["name"], "description": tool.get("description") or ""})
                if server.get("enabled") is False:
                    r["enabled_everywhere"] = False
                r["transport"] = server.get("transport") or r.get("transport")
                r["endpoint"] = server.get("url") or server.get("command") or r.get("endpoint")
                r["auth"] = server.get("auth") or r.get("auth")
    for name, es in entries.items():
        health, error = _health(es)
        row("mcp", name).update(health=health, error=error, profile_health=es)

    # who uses it: the applied versions; who will once a draft is applied: planned_by
    for field, parsed_list in (("used_by", blueprints or []), ("planned_by", drafts or [])):
        for parsed in parsed_list:
            meta = parsed["metadata"]
            for agent in parsed.get("agents") or []:
                user = {"agent": agent["id"], "blueprint": meta["name"], "version": meta.get("version")}
                for name in agent.get("mcps") or []:
                    r = row("mcp", name)
                    if user not in r[field]:
                        r[field].append(user)
                provider = (agent.get("model") or {}).get("provider")
                if provider:
                    r = row("model", provider)
                    if user not in r[field]:
                        r[field].append(user)
                    model_name = (agent.get("model") or {}).get("name")
                    if field == "used_by" and model_name and model_name not in r["models"]:
                        r["models"].append(model_name)

    for parsed in blueprints or []:
        for policy in parsed.get("policies") or []:
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
        # a draft that keeps an agent's use is not news: planned_by lists only agents that do not use it yet
        using = {(u["agent"], u["blueprint"]) for u in r["used_by"]}
        r["planned_by"] = [u for u in r["planned_by"] if (u["agent"], u["blueprint"]) not in using]
        r["instances"] = sorted(r["instances"])
        r["environments"] = sorted({e for e in r["environments"] if e})
        r["profiles"] = sorted(r["profiles"])
        r["models"] = sorted(r["models"])
    return out


def merge_discovery(previous: dict[str, dict], result: dict[str, list], at: float, *, covered: list[str],
                    keep: Optional[set[str]] = None) -> dict[str, dict]:
    """A discovery of some profiles updates those profiles only, as {profile: {"at", "servers"}}. A covered profile
    missing from the result has no servers now. ``keep`` (the profiles of the last import) drops the others."""
    out = {p: v for p, v in (previous or {}).items() if keep is None or p in keep}
    for profile in set(covered) | set(result or {}):
        if keep is None or profile in keep:
            out[profile] = {"at": at, "servers": list((result or {}).get(profile) or [])}
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
