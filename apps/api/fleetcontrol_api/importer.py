"""Turn an instance's imported live profiles into a Blueprint v1 draft (build document §9, Slice 1).

The draft must describe what is running, not an approximation of it: planning it against the same
live state has to change nothing. A profile that cannot be reproduced exactly (no model, SOUL text
not imported, a value the schema would normalise) is left out and reported with the reason.
"""

from __future__ import annotations

import re
import time
from typing import Optional

from fleetcontrol_blueprint import Blueprint

from .planner import compute_plan

_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")


class LiveImportError(ValueError):
    pass


def agent_id_for(profile: str) -> str:
    """A blueprint agent id for a Hermes profile name (ids are ^[a-z][a-z0-9-]{1,62}$)."""
    if _ID.match(profile):
        return profile
    slug = re.sub(r"[^a-z0-9-]+", "-", profile.lower()).strip("-")
    if not slug or not slug[0].isalpha():
        slug = "p-" + slug
    if len(slug) < 2:
        slug += "-profile"
    return slug[:63].rstrip("-")


def _objective(soul_text: str, profile: str) -> str:
    """The first line of the SOUL, for the topology and inspector; the SOUL itself is kept verbatim."""
    for line in soul_text.splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:200]
    return f"Imported from the Hermes profile {profile}."


def _agent(profile: str, state: dict, agent_id: str) -> dict:
    model = state["model"]
    agent = {
        "id": agent_id,
        "role": state.get("description") or "",
        "model": {"provider": model["provider"], "name": model["name"]},
        "soul": {"objective": _objective(state["soul_text"], profile), "raw": state["soul_text"].rstrip() + "\n"},
        "skills": sorted(state.get("skills") or []),
        "toolsets": sorted(state.get("toolsets") or []),
        "mcps": sorted(state.get("mcps") or []),
    }
    if agent_id != profile:
        agent["hermes_profile"] = profile
    return agent


def _build(agents: list[dict], *, name: str, owner: str, instance_id: str, environment: str, now: float) -> Blueprint:
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    return Blueprint.model_validate({
        "metadata": {"name": name, "version": 1, "owner": owner,
                     "description": f"Imported from {instance_id} on {day}."},
        "requires": {"hermes": ">=0.21", "capabilities": ["profiles.read", "profiles.write"], "agent": "required"},
        "mission": f"What {instance_id} runs today, imported on {day}. Replace this with what the fleet is for.",
        "agents": agents,
        "targets": [{"instance": instance_id, "environment": environment}],
    })


def blueprint_from_live(live: dict[str, dict], *, name: str, owner: str, instance_id: str, environment: str,
                        profiles: Optional[list[str]] = None, now: Optional[float] = None) -> tuple[Blueprint, list[dict]]:
    """Returns (blueprint, skipped). ``live`` is the import job's {profile: state}; ``profiles``
    limits the import (default: every live profile). Raises LiveImportError when nothing is left."""
    now = time.time() if now is None else now
    chosen = sorted(live) if profiles is None else list(dict.fromkeys(profiles))
    unknown = [p for p in chosen if p not in live]
    if unknown:
        raise LiveImportError(f"not imported from {instance_id}: {', '.join(unknown)}; run an import first")

    skipped: list[dict] = []
    agents: dict[str, dict] = {}  # profile -> agent
    ids: set[str] = set()
    for profile in chosen:
        state = live[profile]
        model = state.get("model") or {}
        if not model.get("provider") or not model.get("name"):
            skipped.append({"profile": profile, "reason": "no model provider and name set in Hermes"})
            continue
        if not isinstance(state.get("soul_text"), str):
            skipped.append({"profile": profile, "reason": "SOUL text not in the last import; import again"})
            continue
        base = agent_id = agent_id_for(profile)
        n = 2
        while agent_id in ids:
            agent_id, n = f"{base[:60]}-{n}", n + 1
        ids.add(agent_id)
        agents[profile] = _agent(profile, state, agent_id)

    # Exactness: planning against the same live state must change nothing. Drop what would change.
    if agents:
        bp = _build(list(agents.values()), name=name, owner=owner, instance_id=instance_id, environment=environment, now=now)
        plan = compute_plan(bp, {p: live[p] for p in agents}, target_instance=instance_id, agent_installed=True, environment=environment)
        for row in plan["changes"]:
            if row["kind"] in ("create", "update"):
                profile = row["object"].removeprefix("profile ")
                skipped.append({"profile": profile, "reason": f"cannot be reproduced exactly ({row['description']})"})
                agents.pop(profile, None)
    if not agents:
        reasons = "; ".join(f"{s['profile']}: {s['reason']}" for s in skipped) or "no profiles"
        raise LiveImportError(f"nothing could be imported from {instance_id} ({reasons})")
    bp = _build(list(agents.values()), name=name, owner=owner, instance_id=instance_id, environment=environment, now=now)
    return bp, skipped
