"""Fleet Architect (build document §9, Slice 2): a mission in, a structured proposal out, a blueprint draft at the end.

The proposal contract is versioned on its own (``fleetcontrol.proposal/v1``), independent of Hermes. The architect
profile gets it in the run's instructions and must answer with one JSON object; the answer is validated here, and
anything unusable is reported with the reason, never guessed at. The architect only proposes: nothing reaches a
Hermes instance until a person saves a blueprint draft, plans it and applies the plan.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from fleetcontrol_blueprint import Blueprint

PROPOSAL_SCHEMA = "fleetcontrol.proposal/v1"
ARCHITECT_BLUEPRINT = "fleet-control-architect"
ARCHITECT_PROFILE = "fc-architect"
EDITABLE = frozenset({"name", "role", "model", "skills", "toolsets", "mcps", "soul"})
_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")


class ProposalError(ValueError):
    pass


def slug(value: Any) -> str:
    """Lowercase words joined by hyphens, as blueprint ids are ("Case Orchestrator" -> "case-orchestrator")."""
    return re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")


def _as_id(value: Any) -> str:
    s = slug(value)
    if not _ID.match(s):
        raise ValueError("ids are lowercase words joined by hyphens, 2 to 63 characters, starting with a letter")
    return s


class Constraints(BaseModel):
    """What the person sets next to the mission (design/screens/FleetArchitect: Constraints)."""

    model_config = ConfigDict(extra="forbid")

    data_residency: Literal["any", "region", "on-premises"] = "any"
    cloud_models: Literal["allowed", "redacted-only", "none"] = "allowed"
    external_actions_need_approval: bool = True
    budget_usd_per_day: Optional[float] = Field(None, ge=0, le=1_000_000)


# ---------------------------------------------------------------------------- the contract


class _Answer(BaseModel):
    """What the architect sends back: unknown keys are dropped, missing or wrong ones are reported."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True, populate_by_name=True)


class ProposedModel(_Answer):
    provider: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    data_class: Literal["raw", "redacted-only"] = "raw"


class ProposedSoul(_Answer):
    objective: str = Field(..., min_length=1)
    principles: list[str] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)


class ProposedAgent(_Answer):
    id: str
    name: str = ""
    role: str = Field(..., min_length=1)
    model: ProposedModel
    soul: ProposedSoul
    skills: list[str] = Field(default_factory=list)
    toolsets: list[str] = Field(default_factory=list)
    mcps: list[str] = Field(default_factory=list)
    delegates_to: list[str] = Field(default_factory=list)

    @field_validator("id", mode="before")
    @classmethod
    def _id(cls, v: Any) -> str:
        return _as_id(v)

    @field_validator("delegates_to", mode="before")
    @classmethod
    def _refs(cls, v: Any) -> list[str]:
        return [slug(x) for x in (v or [])]


class ProposedTest(_Answer):
    id: str
    target: str
    scenario: str = Field(..., min_length=1)
    required_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)

    @field_validator("id", mode="before")
    @classmethod
    def _id(cls, v: Any) -> str:
        return _as_id(v)

    @field_validator("target", mode="before")
    @classmethod
    def _target(cls, v: Any) -> str:
        return slug(v)


class Estimate(_Answer):
    cost_per_day_usd: Optional[str] = None  # a rough range as text, e.g. "18-26"
    basis: Optional[str] = None

    @field_validator("cost_per_day_usd", mode="before")
    @classmethod
    def _text(cls, v: Any) -> Optional[str]:
        return None if v is None else str(v).strip().lstrip("$")


class Proposal(_Answer):
    contract: str = Field(PROPOSAL_SCHEMA, alias="schema")
    summary: str = Field(..., min_length=1)
    agents: list[ProposedAgent] = Field(..., min_length=1, max_length=12)
    tests: list[ProposedTest] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list, max_length=10)
    estimate: Estimate = Field(default_factory=Estimate)

    @field_validator("open_questions", mode="before")
    @classmethod
    def _questions(cls, v: Any) -> list[str]:  # models sometimes send [{"question": "..."}]
        return [str(q.get("question") or q.get("text") or "") if isinstance(q, dict) else str(q) for q in (v or [])]


CONTRACT_EXAMPLE: dict[str, Any] = {
    "schema": PROPOSAL_SCHEMA,
    "summary": "One or two sentences: how the fleet is organised and why.",
    "agents": [{
        "id": "case-orchestrator",
        "name": "Case Orchestrator",
        "role": "One line: what this agent is for.",
        "model": {"provider": "<provider>", "name": "<model>", "data_class": "raw"},
        "soul": {"objective": "What it achieves.", "principles": ["How it works."], "boundaries": ["What it must never do."]},
        "skills": ["<skill>"],
        "toolsets": ["<toolset>"],
        "mcps": [],
        "delegates_to": ["<another agent id>"],
    }],
    "tests": [{"id": "orchestrator-routes-a-case", "target": "case-orchestrator", "scenario": "A realistic input.",
               "required_tools": [], "forbidden_tools": []}],
    "open_questions": ["Something you need to know to do better."],
    "estimate": {"cost_per_day_usd": "10-20", "basis": "What the range assumes."},
}


# ---------------------------------------------------------------------------- asking


def constraint_lines(c: Constraints) -> list[str]:
    lines = [
        {"on-premises": "Customer data stays on-premises.", "region": "Customer data stays within its region.",
         "any": "No data residency restriction."}[c.data_residency],
        {"allowed": "Cloud models may be used.",
         "redacted-only": "Cloud models may only see redacted summaries; raw data goes to local models.",
         "none": "No cloud models: local models only."}[c.cloud_models],
        "Every external action (sending, submitting, posting) needs human approval." if c.external_actions_need_approval
        else "External actions do not need approval.",
    ]
    if c.budget_usd_per_day is not None:
        lines.append(f"Budget: at most ${c.budget_usd_per_day:g} per day for the whole fleet.")
    return lines


def estate_summary(live_by_instance: dict[str, dict[str, dict]]) -> dict[str, list]:
    """Models, skills and toolsets the connected instances already run (from their last imports)."""
    models: set[tuple[str, str]] = set()
    skills: set[str] = set()
    toolsets: set[str] = set()
    by_instance: dict[str, list[str]] = {}
    for instance_id, profiles in live_by_instance.items():
        mine: set[str] = set()
        for state in (profiles or {}).values():
            m = state.get("model") or {}
            if m.get("provider") and m.get("name"):
                models.add((m["provider"], m["name"]))
                mine.add(f"{m['provider']}/{m['name']}")
            skills.update(state.get("skills") or [])
            toolsets.update(state.get("toolsets") or [])
        by_instance[instance_id] = sorted(mine)
    return {"models": sorted(models), "skills": sorted(skills), "toolsets": sorted(toolsets), "by_instance": by_instance}


def instructions(constraints: Constraints, estate: dict[str, list]) -> str:
    """The run's instructions: the contract, the rules, the constraints and the estate."""
    models = ", ".join(f"{p}/{n}" for p, n in estate["models"]) or "none known yet"
    return "\n".join([
        "You are the Fleet Control architect. The user message describes a mission. Design a fleet of Hermes Agent",
        "profiles for it. You only propose: never call tools and never change anything.",
        "",
        f'Answer with exactly one JSON object that follows the Fleet Control proposal contract "{PROPOSAL_SCHEMA}".',
        "No prose before or after it and no Markdown fences. Shape:",
        json.dumps(CONTRACT_EXAMPLE, indent=2),
        "",
        "Rules:",
        "- 1 to 7 agents. With more than one, exactly one orchestrator lists the others in delegates_to.",
        "- ids are lowercase words joined by hyphens and unique; delegates_to and test targets use them.",
        "- model: one the instance that will run the agent has (models by instance, below); an instance cannot run a model",
        "  it does not have. data_class is redacted-only for a cloud model that may only see redacted input.",
        "- skills and toolsets: prefer names the estate already has (below); leave a list empty rather than invent names.",
        "- soul.boundaries say what the agent must never do; tests check the risky behaviour.",
        "- open_questions: at most 5, only what would change the design.",
        "- estimate.cost_per_day_usd: a rough range as text, with its basis.",
        "",
        "Constraints the fleet must respect:",
        *[f"- {line}" for line in constraint_lines(constraints)],
        "",
        "Estate (what the connected Hermes instances already have):",
        f"- models: {models}",
        "- models by instance: " + ("; ".join(f"{i}: {', '.join(ms)}" for i, ms in sorted(estate.get("by_instance", {}).items()) if ms)
                                    or "none known yet"),
        f"- skills: {', '.join(estate['skills'][:80]) or 'none known yet'}",
        f"- toolsets: {', '.join(estate['toolsets']) or 'none known yet'}",
    ])


def request_text(mission: str, *, answers: list[dict], previous: Optional[dict], decisions: dict[str, str]) -> str:
    """The run's input: the mission, then (when asking again) the last proposal, what the person kept and removed,
    and their answers to the open questions."""
    parts = ["Mission:", mission.strip()]
    if previous:
        kept = [a["id"] for a in previous["agents"] if decisions.get(a["id"]) == "accepted"]
        removed = [a["id"] for a in previous["agents"] if decisions.get(a["id"]) == "removed"]
        shown = {k: v for k, v in previous.items() if k != "adjustments"}
        parts += ["", "Your previous proposal (with the person's edits):", json.dumps(shown, separators=(",", ":"))]
        if kept:
            parts.append("Keep these agents as they are: " + ", ".join(kept) + ".")
        if removed:
            parts.append("The person removed: " + ", ".join(removed) + ". Do not propose them again.")
    if answers:
        parts += ["", "Answers to your open questions:"]
        parts += [f"- Q: {a['question']}\n  A: {a['answer']}" for a in answers]
    parts += ["", "Reply with the JSON object only."]
    return "\n".join(parts)


# ---------------------------------------------------------------------------- reading the answer


def parse_proposal(output: str) -> dict:
    """The architect's answer as a proposal dict, or ProposalError saying why it cannot be used.

    Dangling references (a delegation or test naming an agent the proposal does not have) are dropped and
    listed under ``adjustments``, so one slip does not waste a whole run."""
    text = (output or "").strip()
    if not text:
        raise ProposalError("the architect returned an empty answer")
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ProposalError(f"the answer is not a JSON object ({exc.msg})") from None
    if not isinstance(data, dict):
        raise ProposalError("the answer is not a JSON object")
    try:
        proposal = Proposal.model_validate(data)
    except ValidationError as exc:
        where = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])
        raise ProposalError(f"the answer does not follow {PROPOSAL_SCHEMA} ({where})") from None
    if proposal.contract != PROPOSAL_SCHEMA:
        raise ProposalError(f'the answer declares "{proposal.contract}"; Fleet Control reads {PROPOSAL_SCHEMA}')
    ids = [a.id for a in proposal.agents]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise ProposalError("the proposal repeats agent ids: " + ", ".join(repeated))

    out = proposal.model_dump(by_alias=True)
    adjustments: list[str] = []
    for a in out["agents"]:
        dangling = [d for d in a["delegates_to"] if d not in ids or d == a["id"]]
        if dangling:
            adjustments.append(f"{a['id']}: dropped delegation to {', '.join(dangling)} (not in the proposal)")
            a["delegates_to"] = [d for d in a["delegates_to"] if d not in dangling]
    tests = []
    for t in out["tests"]:
        if t["target"] in ids:
            tests.append(t)
        else:
            adjustments.append(f"test {t['id']}: dropped, it targets {t['target']} (not in the proposal)")
    out["tests"] = tests
    out["adjustments"] = adjustments
    return out


def merge_edit(base: dict, edit: dict) -> dict:
    """``edit`` over ``base``; model and soul merge field by field."""
    out = {**base, **{k: v for k, v in edit.items() if k not in ("model", "soul")}}
    for key in ("model", "soul"):
        if key in edit:
            out[key] = {**(base.get(key) or {}), **(edit[key] or {})}
    return out


def check_agent(agent: dict) -> dict:
    """An edited agent must still satisfy the contract; ValueError says what is wrong."""
    try:
        return ProposedAgent.model_validate(agent).model_dump()
    except ValidationError as exc:
        raise ValueError("; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])) from None


# ---------------------------------------------------------------------------- to a blueprint


def policies_for(c: Constraints) -> list[dict]:
    """Constraints become fleet-wide policies in the blueprint."""
    policies: list[dict] = []
    if c.data_residency != "any":
        where = "on-premises" if c.data_residency == "on-premises" else "within its region"
        policies.append({"id": "data-residency", "kind": "data-residency", "description": f"Customer data stays {where}.",
                         "applies_to": ["*"], "params": {"residency": c.data_residency}, "enforcement": "block"})
    if c.cloud_models == "redacted-only":
        policies.append({"id": "cloud-models-redacted", "kind": "data-residency",
                         "description": "Cloud models receive redacted summaries only.", "applies_to": ["*"], "enforcement": "block"})
    elif c.cloud_models == "none":
        policies.append({"id": "local-models-only", "kind": "model-allowlist", "description": "Only local models.",
                         "applies_to": ["*"], "params": {"providers": ["local"]}, "enforcement": "block"})
    if c.external_actions_need_approval:
        policies.append({"id": "external-actions-approval", "kind": "external-action-approval",
                         "description": "Any external action needs human approval. Name the tools it covers in params.tools.",
                         "applies_to": ["*"], "params": {"tools": []}, "enforcement": "approve"})
    if c.budget_usd_per_day is not None:
        policies.append({"id": "budget", "kind": "budget", "description": f"At most ${c.budget_usd_per_day:g} per day.",
                         "applies_to": ["*"], "params": {"usd_per_day": c.budget_usd_per_day}, "enforcement": "flag"})
    return policies


def to_blueprint(proposal: dict, *, name: str, owner: str, mission: str, constraints: Constraints,
                 accepted: list[str], edits: dict[str, dict]) -> Blueprint:
    """A Blueprint v1 draft with the accepted agents (edits applied), their tests, and the constraints as policies.
    Raises ValueError when the result is not a valid blueprint."""
    keep = [a for a in proposal["agents"] if a["id"] in set(accepted)]
    ids = {a["id"] for a in keep}
    tests = [t for t in proposal["tests"] if t["target"] in ids]
    agents = []
    for a in keep:
        a = merge_edit(a, edits.get(a["id"], {}))
        agents.append({
            "id": a["id"],
            "role": a["role"],
            "model": {"provider": a["model"]["provider"], "name": a["model"]["name"], "data_class": a["model"].get("data_class", "raw")},
            "soul": {"objective": a["soul"]["objective"], "principles": a["soul"].get("principles", []),
                     "boundaries": a["soul"].get("boundaries", [])},
            "skills": a.get("skills", []),
            "toolsets": a.get("toolsets", []),
            "mcps": a.get("mcps", []),
            "delegates_to": [d for d in a.get("delegates_to", []) if d in ids],
            "tests": [t["id"] for t in tests if t["target"] == a["id"]],
        })
    return Blueprint.model_validate({
        "metadata": {"name": name, "version": 1, "owner": owner, "description": proposal["summary"]},
        "requires": {"hermes": ">=0.21", "capabilities": ["runs", "profiles.read", "profiles.write"], "agent": "required"},
        "mission": mission,
        "policies": policies_for(constraints),
        "agents": agents,
        "tests": [{"id": t["id"], "target": t["target"], "scenario": t["scenario"], "required_tools": t["required_tools"],
                   "forbidden_tools": t["forbidden_tools"], "evaluator": "none"} for t in tests],
    })


def architect_blueprint(model: dict, *, instance_id: str, environment: str, owner: str, version: int) -> Blueprint:
    """The Fleet Control architect as a blueprint: one profile with no skills and no tools, on ``model``."""
    return Blueprint.model_validate({
        "metadata": {"name": ARCHITECT_BLUEPRINT, "version": version, "owner": owner,
                     "description": "The Fleet Control architect: one profile that turns missions into proposals. It has no tools."},
        "requires": {"hermes": ">=0.21", "capabilities": ["runs", "profiles.write"], "agent": "required"},
        "mission": f"Answer Fleet Control's architect requests with structured proposals ({PROPOSAL_SCHEMA}).",
        "agents": [{
            "id": ARCHITECT_PROFILE,
            "role": "Fleet Control architect: proposes fleets of Hermes profiles from a mission; never acts.",
            "model": {"provider": model["provider"], "name": model["name"]},
            "soul": {
                "objective": "Turn a mission into a fleet proposal Fleet Control can read.",
                "principles": [
                    "Answer with one JSON object that follows the contract in the instructions, and nothing else.",
                    "Prefer few, focused agents; one orchestrator coordinates the specialists.",
                    "When the mission is ambiguous, ask in open_questions instead of guessing.",
                ],
                "boundaries": ["Never call tools or change anything: you only propose.",
                               "Never put secrets or credentials in a proposal."],
            },
            "skills": [],
            "toolsets": [],
        }],
        "targets": [{"instance": instance_id, "environment": environment}],
    })
