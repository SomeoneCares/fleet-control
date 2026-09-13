"""Drift resolution: the four answers the Drift screen offers, as pure functions.

A drift report is what the agent's ``drift_scan`` job returns for the blueprint version applied to an
instance: ``{profile: [{"field", "blueprint", "live"}]}`` over the managed fields
(``Blueprint.managed_fields()``). The resolutions:

* ``accept``      — the live values become a new (draft) blueprint version;
* ``revert``      — a plan that writes the blueprint values back, from the same planner as any apply;
* ``ignore_once`` — clear the alert; the next scan reports the same drift again;
* ``exception``   — stop reporting the chosen fields on this instance until a date.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from fleetcontrol_blueprint import Blueprint

ACTIONS = ("accept", "revert", "ignore_once", "exception")


class DriftResolutionError(ValueError):
    pass


def select_drift(drift: dict, fields: Optional[list[dict]]) -> dict:
    """Restrict a drift report to ``[{profile, field}]``; an empty selection means everything."""
    if not fields:
        return {p: list(diffs) for p, diffs in drift.items() if diffs}
    want = {(f["profile"], f["field"]) for f in fields}
    out: dict[str, list] = {}
    for profile, diffs in drift.items():
        keep = [d for d in diffs if (profile, d["field"]) in want]
        if keep:
            out[profile] = keep
    missing = want - {(p, d["field"]) for p, diffs in out.items() for d in diffs}
    if missing:
        raise DriftResolutionError(f"not in the current drift report: {sorted(missing)}")
    return out


def live_state_from_drift(bp: Blueprint, drift: dict) -> dict[str, dict]:
    """The live managed state the scan saw: blueprint values, overridden by the drifted ones.

    Exact for managed fields, because the scan compared every one of them. A profile reported
    missing is left out, so the planner produces a create row for it."""
    live: dict[str, dict] = {}
    for name, want in bp.managed_fields().items():
        diffs = drift.get(name, [])
        if any(d["field"] == "profile" for d in diffs):
            continue
        state = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in want.items()}
        for d in diffs:
            state[d["field"]] = d["live"]
        live[name] = state
    return live


def _soul_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256((text.rstrip() + "\n").encode("utf-8")).hexdigest()


def accept_into_blueprint(bp: Blueprint, drift: dict, *, new_version: int, soul_texts: dict[str, str]) -> Blueprint:
    """A new blueprint version whose managed fields take the drifted live values.

    SOUL drift arrives as a hash only; the live text comes from the instance's last import
    (``soul_texts``) and is used only when it hashes to the drifted value."""
    data = bp.model_dump(mode="json", exclude_none=True)
    agents = {a.get("hermes_profile") or a["id"]: a for a in data["agents"]}
    for profile, diffs in drift.items():
        agent = agents.get(profile)
        if agent is None:
            raise DriftResolutionError(f"profile {profile!r} is not managed by this blueprint")
        for d in diffs:
            field, live = d["field"], d["live"]
            if field == "profile":
                raise DriftResolutionError(f"profile {profile!r} is missing on the instance; revert it or create an exception")
            if field == "description":
                agent["role"] = live
            elif field == "model":
                agent["model"].update({"provider": (live or {}).get("provider"), "name": (live or {}).get("name")})
            elif field in ("skills", "toolsets", "mcps"):
                agent[field] = list(live or [])
            elif field == "soul_sha256":
                text = soul_texts.get(profile)
                if text is None or _soul_hash(text) != live:
                    raise DriftResolutionError(f"the live SOUL text of {profile!r} is not known; run an import, then accept again")
                agent["soul"]["raw"] = text.rstrip() + "\n"
            else:
                raise DriftResolutionError(f"unknown drift field {field!r}")
    data["metadata"]["version"] = new_version
    return Blueprint.model_validate(data)
