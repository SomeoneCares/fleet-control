from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

API_VERSION = "fleetcontrol/v1"

_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
_SECRET_REF = re.compile(r"^secret://[A-Za-z0-9_./-]+$")


class _Strict(BaseModel):
    """All blueprint objects reject unknown keys: a typo must fail validation, not silently no-op."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------- metadata


class Environment(str, Enum):
    lab = "lab"
    staging = "staging"
    production = "production"


class BlueprintMetadata(_Strict):
    name: str = Field(..., description="Blueprint id; lowercase, hyphenated.")
    version: int = Field(..., ge=1, description="Monotonic version number; immutable once published.")
    owner: str = Field(..., description="Person or team responsible for this blueprint.")
    description: Optional[str] = None
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("name must match ^[a-z][a-z0-9-]{1,62}$")
        return v


class Requires(_Strict):
    """What a Hermes instance must offer for this blueprint to be applicable.

    ``hermes`` is a PEP 440-style specifier against the real Hermes version (0.21.x line);
    ``capabilities`` are Fleet Control capability ids reported by the agent.
    """

    hermes: str = Field(">=0.21", description="Version specifier for the Hermes package version.")
    capabilities: list[str] = Field(default_factory=lambda: ["runs", "sessions", "profiles.read"])
    agent: Literal["required", "optional"] = Field(
        "required",
        description="Whether the Fleet Control Agent must be installed on target instances. "
        "'optional' allows API-only (read-only) targets.",
    )


# --------------------------------------------------------------------------- policies


class PolicyKind(str, Enum):
    data_residency = "data-residency"
    external_action_approval = "external-action-approval"
    tool_allowlist = "tool-allowlist"
    tool_denylist = "tool-denylist"
    model_allowlist = "model-allowlist"
    budget = "budget"
    custom = "custom"


class Policy(_Strict):
    id: str
    kind: PolicyKind
    description: str = Field(..., description="Human-readable rule; shown on plans and in Assurance.")
    applies_to: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Agent ids, or '*' for the whole fleet.",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Kind-specific parameters, e.g. {'tools': ['web.fetch']} for tool-denylist.",
    )
    enforcement: Literal["block", "approve", "flag"] = Field(
        "block",
        description="block: the agent's pre_tool_call hook refuses the call; "
        "approve: escalates to Hermes's approval gate; flag: observe only.",
    )

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("policy id must match ^[a-z][a-z0-9-]{1,62}$")
        return v


# --------------------------------------------------------------------------- agents


class ModelPolicy(_Strict):
    provider: str = Field(..., description="Hermes provider id, e.g. 'anthropic', 'openai', 'local'.")
    name: str = Field(..., description="Model id as Hermes knows it.")
    fallback: Optional[str] = Field(None, description="Fallback model id, if Hermes supports it for this provider.")
    data_class: Literal["raw", "redacted-only"] = Field(
        "raw",
        description="'redacted-only' marks a cloud model that may receive redacted summaries only.",
    )


class OutputContract(_Strict):
    format: Literal["json", "markdown", "text", "file"] = "json"
    required: list[str] = Field(default_factory=list, description="Required top-level keys (json) or sections.")
    artifact: Optional[str] = Field(None, description="Expected artifact path/name when format is 'file'.")


class Soul(_Strict):
    """Structured SOUL. Rendered to SOUL.md by the agent; the hash lets drift be detected."""

    objective: str
    principles: list[str] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)
    output_contract: Optional[OutputContract] = None
    # Verbatim means verbatim: leading whitespace is part of the file and of its hash, so this
    # field opts out of the model-wide stripping.
    raw: Optional[Annotated[str, StringConstraints(strip_whitespace=False)]] = Field(
        None,
        description="Escape hatch: verbatim SOUL.md. When set, structured sections are ignored on render.",
    )

    def render(self) -> str:
        if self.raw is not None:
            return self.raw.rstrip() + "\n"
        parts = ["# Objective", "", self.objective.strip(), ""]
        if self.principles:
            parts += ["# Operating principles", ""] + [f"- {p}" for p in self.principles] + [""]
        if self.boundaries:
            parts += ["# Boundaries", ""] + [f"- {b}" for b in self.boundaries] + [""]
        if self.output_contract:
            oc = self.output_contract
            parts += ["# Output contract", "", f"format: {oc.format}"]
            if oc.required:
                parts.append("required: " + ", ".join(oc.required))
            if oc.artifact:
                parts.append(f"artifact: {oc.artifact}")
            parts.append("")
        return "\n".join(parts)

    def content_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self.render().encode("utf-8")).hexdigest()


class Agent(_Strict):
    id: str
    role: str = Field(..., description="One line: what this agent is for. Shown on the topology.")
    model: ModelPolicy
    soul: Soul
    skills: list[str] = Field(default_factory=list, description="Hermes skill names to enable.")
    toolsets: list[str] = Field(default_factory=list)
    mcps: list[str] = Field(default_factory=list, description="MCP server names registered on the profile.")
    delegates_to: list[str] = Field(default_factory=list, description="Agent ids this agent may delegate to.")
    content_zones: list[str] = Field(default_factory=list, description="Content zones readable by this agent.")
    tests: list[str] = Field(default_factory=list, description="Test ids attached to this agent.")
    hermes_profile: Optional[str] = Field(
        None, description="Profile name on the Hermes instance; defaults to the agent id."
    )

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("agent id must match ^[a-z][a-z0-9-]{1,62}$")
        return v

    @property
    def profile_name(self) -> str:
        return self.hermes_profile or self.id


# --------------------------------------------------------------------------- tests


class TestLimits(_Strict):
    max_seconds: int = Field(60, ge=1)
    max_tokens: int = Field(20_000, ge=1)
    max_cost_usd: float = Field(0.10, ge=0)


class Test(_Strict):
    id: str
    target: str = Field(..., description="Agent id or workflow id.")
    scenario: str = Field(..., description="Input prompt / scenario text.")
    required_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    expected_artifact: Optional[str] = None
    evaluator: Literal["schema", "exact", "contains", "artifact-exists", "none"] = "schema"
    expected: Optional[str] = Field(None, description="Expected value for exact/contains evaluators.")
    limits: TestLimits = Field(default_factory=TestLimits)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("test id must match ^[a-z][a-z0-9-]{1,62}$")
        return v


# --------------------------------------------------------------------------- workflows


class AgentStep(_Strict):
    agent: str
    artifact: Optional[str] = None
    input: Optional[str] = None


class ParallelStep(_Strict):
    parallel: list[AgentStep] = Field(..., min_length=2)


class HumanGate(_Strict):
    human_gate: str = Field(..., description="Role that must approve, e.g. 'approver'.")
    timeout: str = Field("24h", pattern=r"^\d+(m|h|d)$")
    escalate_to: Optional[str] = None
    on_reject: Optional[str] = Field(None, description="Agent id to return to with notes.")


class OpenDecisionRoom(_Strict):
    open_decision_room: bool = True
    question_template: Optional[str] = None


WorkflowStep = Union[AgentStep, ParallelStep, HumanGate, OpenDecisionRoom]


class Workflow(_Strict):
    id: str
    steps: list[WorkflowStep] = Field(..., min_length=1)
    tests: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("workflow id must match ^[a-z][a-z0-9-]{1,62}$")
        return v


# --------------------------------------------------------------------------- delivery & targets


class DeliveryRule(_Strict):
    when: Literal[
        "decision_room.opened",
        "approval.second_needed",
        "output.shared",
        "assurance.no_evidence",
        "assurance.policy_blocked",
        "drift.detected",
        "apply.completed",
        "apply.failed",
    ]
    to: str = Field(..., description="Channel ref, e.g. 'teams:aml-investigations', 'slack:#fleet-ops', 'email:approvers'.")
    template: str = Field(..., description="Message template id.")
    enabled: bool = True


class Target(_Strict):
    instance: str = Field(..., description="Instance id as registered in Fleet Control.")
    environment: Environment
    requires_approvals: int = Field(0, ge=0, description="Approvals needed before apply; production default is 2.")

    @model_validator(mode="after")
    def _prod_default(self) -> "Target":
        if self.environment == Environment.production and self.requires_approvals == 0:
            object.__setattr__(self, "requires_approvals", 2)
        return self


class SecretRef(_Strict):
    name: str
    ref: str = Field(..., description="secret://<store>/<path>; values are never stored in a blueprint.")

    @field_validator("ref")
    @classmethod
    def _ref(cls, v: str) -> str:
        if not _SECRET_REF.match(v):
            raise ValueError("secret refs must look like secret://store/path")
        return v


# --------------------------------------------------------------------------- root


class Blueprint(_Strict):
    apiVersion: Literal["fleetcontrol/v1"] = API_VERSION
    kind: Literal["Blueprint"] = "Blueprint"
    metadata: BlueprintMetadata
    requires: Requires = Field(default_factory=Requires)
    mission: str
    policies: list[Policy] = Field(default_factory=list)
    agents: list[Agent] = Field(..., min_length=1)
    workflows: list[Workflow] = Field(default_factory=list)
    tests: list[Test] = Field(default_factory=list)
    delivery: list[DeliveryRule] = Field(default_factory=list)
    secrets: list[SecretRef] = Field(default_factory=list)
    targets: list[Target] = Field(default_factory=list)

    # ---- cross-object integrity -------------------------------------------------

    @model_validator(mode="after")
    def _references(self) -> "Blueprint":
        agent_ids = {a.id for a in self.agents}
        test_ids = {t.id for t in self.tests}
        wf_ids = {w.id for w in self.workflows}
        errors: list[str] = []

        if len(agent_ids) != len(self.agents):
            errors.append("duplicate agent ids")
        if len(test_ids) != len(self.tests):
            errors.append("duplicate test ids")
        if len(wf_ids) != len(self.workflows):
            errors.append("duplicate workflow ids")

        for a in self.agents:
            for d in a.delegates_to:
                if d not in agent_ids:
                    errors.append(f"agent '{a.id}' delegates to unknown agent '{d}'")
            for t in a.tests:
                if t not in test_ids:
                    errors.append(f"agent '{a.id}' references unknown test '{t}'")
        for t in self.tests:
            if t.target not in agent_ids and t.target not in wf_ids:
                errors.append(f"test '{t.id}' targets unknown agent/workflow '{t.target}'")
        for w in self.workflows:
            for s in w.steps:
                refs = []
                if isinstance(s, AgentStep):
                    refs = [s.agent]
                elif isinstance(s, ParallelStep):
                    refs = [p.agent for p in s.parallel]
                elif isinstance(s, HumanGate) and s.on_reject:
                    refs = [s.on_reject]
                for r in refs:
                    if r not in agent_ids:
                        errors.append(f"workflow '{w.id}' references unknown agent '{r}'")
            for t in w.tests:
                if t not in test_ids:
                    errors.append(f"workflow '{w.id}' references unknown test '{t}'")
        for p in self.policies:
            for target in p.applies_to:
                if target != "*" and target not in agent_ids:
                    errors.append(f"policy '{p.id}' applies to unknown agent '{target}'")
            if p.kind == PolicyKind.data_residency:
                # redacted-only cloud models must not receive raw content zones; checked at plan time,
                # but the blueprint must at least declare the class.
                pass
        if errors:
            raise ValueError("; ".join(errors))
        return self

    # ---- helpers -----------------------------------------------------------------

    def agent(self, agent_id: str) -> Agent:
        for a in self.agents:
            if a.id == agent_id:
                return a
        raise KeyError(agent_id)

    def managed_fields(self) -> dict[str, dict[str, Any]]:
        """The per-profile desired state the Fleet Control Agent reconciles and watches for drift.

        Keys are Hermes profile names. Only fields the agent can read AND write on a Hermes
        instance are included; anything else is informational and never produces a plan row.
        """
        out: dict[str, dict[str, Any]] = {}
        for a in self.agents:
            out[a.profile_name] = {
                "description": a.role,
                "model": {"provider": a.model.provider, "name": a.model.name},
                "soul_sha256": a.soul.content_hash(),
                "skills": sorted(a.skills),
                "toolsets": sorted(a.toolsets),
                "mcps": sorted(a.mcps),
            }
        return out


# --------------------------------------------------------------------------- io


def load_blueprint(text_or_path: str) -> Blueprint:
    """Parse a YAML/JSON blueprint from a string or a file path."""
    import os

    text = text_or_path
    if os.path.exists(text_or_path):
        with open(text_or_path, "r", encoding="utf-8") as f:
            text = f.read()
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("blueprint must be a mapping")
    return Blueprint.model_validate(data)


def dump_blueprint(bp: Blueprint) -> str:
    """Serialise to canonical YAML (stable key order, no nulls) for Git-friendly diffs."""
    data = bp.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def json_schema() -> dict[str, Any]:
    return Blueprint.model_json_schema()
