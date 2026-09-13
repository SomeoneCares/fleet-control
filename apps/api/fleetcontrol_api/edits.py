"""Editing blueprint drafts from Agent Studio. Applied versions are immutable; edits land on drafts only."""

from __future__ import annotations

from typing import Any

from fleetcontrol_blueprint import Blueprint

# What Agent Studio may change on an agent. The id (and so the Hermes profile) is fixed once created.
EDITABLE_AGENT_FIELDS = ("role", "model", "soul", "skills", "toolsets", "mcps", "delegates_to", "content_zones", "tests")


class EditError(ValueError):
    pass


def update_agent(bp: Blueprint, agent_id: str, patch: dict[str, Any]) -> tuple[Blueprint, list[str]]:
    """Apply ``patch`` to one agent and re-validate the whole blueprint (cross-references included).

    Returns the new blueprint and the names of the fields that actually changed.
    Raises KeyError for an unknown agent, EditError for a non-editable field, ValueError when invalid."""
    unknown = sorted(set(patch) - set(EDITABLE_AGENT_FIELDS))
    if unknown:
        raise EditError(f"not editable in Agent Studio: {', '.join(unknown)}")
    data = bp.model_dump(mode="json", exclude_none=True)
    agent = next((a for a in data["agents"] if a["id"] == agent_id), None)
    if agent is None:
        raise KeyError(agent_id)
    before = bp.agent(agent_id).model_dump(mode="json", exclude_none=True)
    agent.update(patch)
    new = Blueprint.model_validate(data)
    after = new.agent(agent_id).model_dump(mode="json", exclude_none=True)
    return new, [k for k in patch if before.get(k) != after.get(k)]


def as_new_version(bp: Blueprint, version: int) -> Blueprint:
    """The same blueprint under a new version number: the start of a new draft."""
    data = bp.model_dump(mode="json", exclude_none=True)
    data["metadata"]["version"] = version
    return Blueprint.model_validate(data)
