"""Mission packs: ready-made fleets that Fleet Studio provisions from.

A pack is a mission with everything needed to run it under human control: the agents, the SAS tools each
may call, a workflow whose human gates hold every material decision, the tests that gate production, and
what the market already offers. It wraps a Blueprint and never extends it: ``instantiate`` turns a pack
into an ordinary v1 draft that is planned, applied and tested like any other.

Packs live as YAML under ``missions/<sector>/<id>.yaml`` next to this module. Shared policy sets
(``missions/_policy_sets.yaml``) keep the SAS rules in one place: the read-only set denies every SAS Viya
MCP tool that changes state or starts work, as SAS itself classifies them (``missions/_sas_viya_tools.yaml``).
A pack includes a set by name and may move named tools out of it, typically to an ``approve`` policy.

Agents in a pack carry no model: the model is chosen when the pack is provisioned, per instance.
"""

from __future__ import annotations

import copy
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import API_VERSION, Blueprint

MISSIONS_DIR = Path(__file__).parent / "missions"
_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")

SECTORS: dict[str, str] = {
    "cross-industry": "Cross-industry (any SAS Viya)",
    "banking": "Banking",
    "insurance": "Insurance",
    "health-life-sciences": "Health and life sciences",
    "public-sector": "Government and public sector",
    "manufacturing": "Manufacturing",
    "energy-utilities": "Energy and utilities",
    "retail-consumer": "Retail and consumer goods",
}
Sector = Literal[
    "cross-industry", "banking", "insurance", "health-life-sciences", "public-sector",
    "manufacturing", "energy-utilities", "retail-consumer",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MissionSAS(_Strict):
    modules: list[str] = Field(..., min_length=1, description="SAS products the mission works with.")
    reach: Literal["mcp-today", "public-api", "connector"] = Field(
        ..., description="mcp-today: the SAS Viya MCP Server covers it; public-api: SAS publishes a REST API we wrap; "
                         "connector: no public API, a connector is built with SAS or customer access.")
    mcp_tools: list[str] = Field(default_factory=list, description="SAS Viya MCP tools the agents use (bare names).")
    connectors: list[str] = Field(default_factory=list, description="MCP servers the mission needs beyond sas-viya.")


class MissionGate(_Strict):
    role: str = Field(..., description="Fleet Control role that decides the gate.")
    who: str = Field(..., description="Who that is in the organisation, e.g. 'Credit committee member'.")
    decides: str


class MissionMarket(_Strict):
    saturation: Literal["crowded", "contested", "thin", "open", "unknown"] = Field(
        ..., description="unknown: not yet researched; never guess a market.")
    vendors: list[str] = Field(default_factory=list)
    sas_own: Optional[str] = Field(None, description="What SAS itself offers for this, if anything.")
    sources: list[str] = Field(default_factory=list)


class PolicyInclude(_Strict):
    set: str
    applies_to: list[str] = Field(default_factory=lambda: ["*"])
    exclude: list[str] = Field(default_factory=list, description="Tools of the set this pack handles differently.")


class MissionPack(_Strict):
    apiVersion: Literal["fleetcontrol/v1"] = API_VERSION
    kind: Literal["MissionPack"] = "MissionPack"
    id: str
    title: str
    sector: Sector
    summary: str
    value: str
    sas: MissionSAS
    human_control: list[MissionGate] = Field(..., min_length=1)
    market: MissionMarket
    kpis: list[str] = Field(default_factory=list)
    include_policies: list[Union[str, PolicyInclude]] = Field(default_factory=list)
    blueprint: dict = Field(..., description="Blueprint body without metadata; agents omit their model.")

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("mission id must match ^[a-z][a-z0-9-]{1,62}$")
        return v

    def summary_row(self) -> dict:
        bp = self.blueprint
        return {
            "id": self.id, "title": self.title, "sector": self.sector, "sector_label": SECTORS[self.sector],
            "summary": self.summary, "reach": self.sas.reach, "saturation": self.market.saturation,
            "modules": self.sas.modules, "connectors": self.sas.connectors,
            "agents": [a["id"] for a in bp.get("agents", [])],
            "gates": len([g for g in self.human_control]),
        }


# ----------------------------------------------------------------------------- shared data


@lru_cache(maxsize=None)
def sas_viya_tools() -> dict[str, dict]:
    """The SAS Viya MCP Server catalogue: tool name -> {tier, access} (access: read-only, write, destructive)."""
    data = yaml.safe_load((MISSIONS_DIR / "_sas_viya_tools.yaml").read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for tier in data["tiers"]:
        for access in ("read-only", "write", "destructive"):
            for name in tier.get(access, []):
                out.setdefault(name, {"tier": tier["tier"], "access": access})
    return out


def _policy_sets() -> dict[str, dict]:
    raw = yaml.safe_load((MISSIONS_DIR / "_policy_sets.yaml").read_text(encoding="utf-8"))["sets"]
    catalogue = sas_viya_tools()
    sets = {}
    for sid, s in raw.items():
        tools = list(s.get("tools", []))
        if s.get("sas_viya_access"):  # every catalogue tool of these access classes, as sas-viya.<tool>
            tools += [f"sas-viya.{n}" for n, t in catalogue.items() if t["access"] in s["sas_viya_access"]]
        sets[sid] = {"kind": s["kind"], "description": s["description"], "enforcement": s.get("enforcement", "block"),
                     "tools": sorted(set(tools))}
    return sets


# ----------------------------------------------------------------------------- loading


def load_pack(text_or_path: Union[str, Path]) -> MissionPack:
    if isinstance(text_or_path, Path) or (isinstance(text_or_path, str) and "\n" not in text_or_path
                                          and text_or_path.endswith((".yaml", ".yml"))):
        text = Path(text_or_path).read_text(encoding="utf-8")
    else:
        text = text_or_path
    return MissionPack.model_validate(yaml.safe_load(text))


@lru_cache(maxsize=None)
def _library() -> tuple[MissionPack, ...]:
    packs = [load_pack(p) for p in sorted(MISSIONS_DIR.glob("*/*.yaml"))]
    return tuple(sorted(packs, key=lambda p: (list(SECTORS).index(p.sector), p.id)))


def library() -> list[MissionPack]:
    """Every pack in the library, ordered by sector then id."""
    return list(_library())


def get_pack(mission_id: str) -> Optional[MissionPack]:
    return next((p for p in _library() if p.id == mission_id), None)


# ----------------------------------------------------------------------------- expanding


DEFAULT_MODEL = {"provider": "choose-at-provisioning", "name": "choose-at-provisioning"}


def expand(pack: MissionPack, *, name: Optional[str] = None, owner: str = "mission-library", version: int = 1,
           model: Optional[dict] = None, target: Optional[dict] = None) -> Blueprint:
    """The pack as a validated Blueprint: shared policy sets expanded, the model set on every agent that has none."""
    body = copy.deepcopy(pack.blueprint)
    sets = _policy_sets()
    includes = [PolicyInclude(set=i) if isinstance(i, str) else i for i in pack.include_policies]
    for inc in includes:
        if inc.set not in sets:
            raise ValueError(f"{pack.id}: unknown policy set {inc.set!r}")
    # A tool the pack puts under approval must leave its deny-lists: the plugin checks deny before approve.
    approved = {t for i in includes if sets[i.set]["enforcement"] == "approve" for t in sets[i.set]["tools"]}
    approved |= {t for p in body.get("policies", []) if p.get("enforcement") == "approve"
                 for t in (p.get("params") or {}).get("tools", [])}
    policies = []
    for inc in includes:
        s = sets[inc.set]
        tools = [t for t in s["tools"] if t not in inc.exclude and t.split(".", 1)[-1] not in inc.exclude
                 and not (s["enforcement"] == "block" and t in approved)]
        policies.append({"id": inc.set, "kind": s["kind"], "description": s["description"], "applies_to": inc.applies_to,
                         "params": {"tools": tools}, "enforcement": s["enforcement"]})
    policies += body.get("policies", [])
    body["policies"] = policies
    for a in body.get("agents", []):
        a.setdefault("model", dict(model or DEFAULT_MODEL))
    body["metadata"] = {
        "name": name or pack.id, "version": version, "owner": owner,
        "description": f"{pack.title}. Provisioned from mission pack {pack.id}.",
        "labels": {"mission": pack.id, "sector": pack.sector},
    }
    body.setdefault("apiVersion", API_VERSION)
    body.setdefault("kind", "Blueprint")
    if target:
        body["targets"] = [target]
    return Blueprint.model_validate(body)


def instantiate(pack: MissionPack, *, owner: str, model: dict, name: Optional[str] = None, version: int = 1,
                target: Optional[dict] = None) -> Blueprint:
    """A draft blueprint for one estate: the owner, the model every agent runs on, and optionally a target."""
    if not (model.get("provider") and model.get("name")):
        raise ValueError("a mission is provisioned onto a model: provider and name are required")
    return expand(pack, name=name, owner=owner, version=version, model=model, target=target)
