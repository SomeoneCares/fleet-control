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


TEST_FIELDS = ("target", "scenario", "required_tools", "forbidden_tools", "expected_artifact", "evaluator", "expected", "limits")


def upsert_test(bp: Blueprint, test_id: str, fields: dict[str, Any]) -> tuple[Blueprint, bool]:
    """Create or replace one test (Test Lab), keeping its target's ``tests`` list in step.

    Returns (new blueprint, created). Raises EditError for a field Test Lab does not edit, ValueError when the
    result is not a valid blueprint (e.g. a target that is neither an agent nor a workflow)."""
    unknown = sorted(set(fields) - set(TEST_FIELDS))
    if unknown:
        raise EditError(f"not a test field: {', '.join(unknown)}")
    data = bp.model_dump(mode="json", exclude_none=True)
    tests = data.setdefault("tests", [])
    current = next((t for t in tests if t["id"] == test_id), None)
    test = {**(current or {}), **{k: v for k, v in fields.items() if v is not None}, "id": test_id}
    for k, v in fields.items():
        if v is None:
            test.pop(k, None)  # an explicit null clears an optional field
    if current is None:
        tests.append(test)
    else:
        tests[tests.index(current)] = test
    for holder in data["agents"] + data.get("workflows", []):
        ids = [t for t in holder.get("tests", []) if t != test_id]
        if holder["id"] == test.get("target"):
            ids.append(test_id)
        holder["tests"] = ids
    return Blueprint.model_validate(data), current is None


def remove_test(bp: Blueprint, test_id: str) -> Blueprint:
    """Drop one test and every reference to it. KeyError when there is no such test."""
    data = bp.model_dump(mode="json", exclude_none=True)
    if not any(t["id"] == test_id for t in data.get("tests", [])):
        raise KeyError(test_id)
    data["tests"] = [t for t in data["tests"] if t["id"] != test_id]
    for holder in data["agents"] + data.get("workflows", []):
        holder["tests"] = [t for t in holder.get("tests", []) if t != test_id]
    return Blueprint.model_validate(data)


def as_new_version(bp: Blueprint, version: int) -> Blueprint:
    """The same blueprint under a new version number: the start of a new draft."""
    data = bp.model_dump(mode="json", exclude_none=True)
    data["metadata"]["version"] = version
    return Blueprint.model_validate(data)
