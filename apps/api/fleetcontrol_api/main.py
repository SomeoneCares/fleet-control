"""Fleet Control API — Slice 1 scaffold.

Two route families:

* ``/api/v1/...`` — the web client (Instances, Blueprints, Plans, Drift, Audit).
* ``/agent/v1/...`` — the fleetctl-agent daemon (pair, heartbeat, events, long-poll jobs, results).

Auth is a placeholder: the web routes trust an ``X-User`` header (replace with OIDC sessions in
Slice 1 proper); agent routes require the bearer token issued at pairing. The store is in-memory.

Run: ``uvicorn fleetcontrol_api.main:app --port 8080``
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from fleetcontrol_blueprint import Blueprint, dump_blueprint, json_schema, load_blueprint

from .planner import compute_plan, to_agent_job
from .store import Store

app = FastAPI(title="Fleet Control API", version="0.1.0")
store = Store()


# ----------------------------------------------------------------------------- auth placeholders


def current_user(x_user: Optional[str] = Header(default=None)) -> str:
    return x_user or "dev@local"


def agent_instance(authorization: Optional[str] = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "agent token required")
    inst = store.instance_for_agent_token(authorization[7:])
    if not inst:
        raise HTTPException(401, "unknown agent token")
    return inst


# ----------------------------------------------------------------------------- web: instances


class InstanceCreate(BaseModel):
    id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9.-]{1,62}$")
    environment: str = Field(..., pattern=r"^(lab|staging|production)$")
    mode: str = Field("agent", pattern=r"^(agent|api-only)$")


@app.post("/api/v1/instances", status_code=201)
def create_instance(body: InstanceCreate, user: str = Depends(current_user)) -> dict:
    if body.id in store.instances:
        raise HTTPException(409, "instance exists")
    inst = store.create_instance(body.id, body.environment, user, body.mode)
    store.record(user, "instance.created", body.id, body.mode)
    # The Connect drawer shows this one-liner; the pairing token is single-use.
    inst["install_command"] = (
        f"curl -fsSL https://get.fleetcontrol.example/install.sh | FLEETCONTROL_URL=<this server> "
        f"FLEETCONTROL_INSTANCE_ID={body.id} FLEETCONTROL_PAIRING_TOKEN={inst['pairing_token']} sh"
    )
    return inst


@app.get("/api/v1/instances")
def list_instances(user: str = Depends(current_user)) -> list[dict]:
    return [{k: v for k, v in i.items() if k != "pairing_token"} for i in store.instances.values()]


@app.get("/api/v1/instances/{instance_id}")
def get_instance(instance_id: str, user: str = Depends(current_user)) -> dict:
    inst = store.instances.get(instance_id)
    if not inst:
        raise HTTPException(404)
    out = {k: v for k, v in inst.items() if k != "pairing_token"}
    out["live_profiles"] = list(store.live_state.get(instance_id, {}).keys())
    out["drift"] = store.drift.get(instance_id)
    return out


@app.post("/api/v1/instances/{instance_id}/import")
def import_profiles(instance_id: str, user: str = Depends(current_user)) -> dict:
    _require_instance(instance_id)
    job = store.enqueue_job(instance_id, "import_profiles", {})
    store.record(user, "instance.import_requested", instance_id, job["id"])
    return {"job_id": job["id"]}


@app.post("/api/v1/instances/{instance_id}/drift-scan")
def drift_scan(instance_id: str, blueprint: str, version: int, user: str = Depends(current_user)) -> dict:
    _require_instance(instance_id)
    bp = _blueprint(blueprint, version)
    job = store.enqueue_job(instance_id, "drift_scan", {"managed": bp.managed_fields()})
    return {"job_id": job["id"]}


def _require_instance(instance_id: str) -> dict:
    inst = store.instances.get(instance_id)
    if not inst:
        raise HTTPException(404, "unknown instance")
    return inst


# ----------------------------------------------------------------------------- web: blueprints


class BlueprintUpload(BaseModel):
    yaml: str


@app.get("/api/v1/blueprints/schema")
def blueprint_schema() -> dict:
    return json_schema()


@app.post("/api/v1/blueprints", status_code=201)
def upload_blueprint(body: BlueprintUpload, user: str = Depends(current_user)) -> dict:
    try:
        bp = load_blueprint(body.yaml)
    except Exception as exc:
        raise HTTPException(422, f"invalid blueprint: {exc}")
    rec = store.save_blueprint(bp.metadata.name, bp.metadata.version, dump_blueprint(bp), bp.model_dump(mode="json"), user)
    store.record(user, "blueprint.saved", f"{bp.metadata.name} v{bp.metadata.version}")
    return {"name": rec["name"], "version": rec["version"], "status": rec["status"], "managed_profiles": list(bp.managed_fields())}


@app.get("/api/v1/blueprints")
def list_blueprints(user: str = Depends(current_user)) -> list[dict]:
    return [
        {"name": n, "versions": sorted(v.keys()), "latest": max(v.keys()), "status": v[max(v.keys())]["status"]}
        for n, v in store.blueprints.items()
    ]


@app.get("/api/v1/blueprints/{name}/{version}")
def get_blueprint(name: str, version: int, user: str = Depends(current_user)) -> dict:
    rec = store.blueprints.get(name, {}).get(version)
    if not rec:
        raise HTTPException(404)
    return {"name": name, "version": version, "status": rec["status"], "yaml": rec["yaml"]}


def _blueprint(name: str, version: int) -> Blueprint:
    rec = store.blueprints.get(name, {}).get(version)
    if not rec:
        raise HTTPException(404, "unknown blueprint version")
    return Blueprint.model_validate(rec["parsed"])


# ----------------------------------------------------------------------------- web: plans


class PlanCreate(BaseModel):
    blueprint: str
    version: int
    instance_id: str


@app.post("/api/v1/plans", status_code=201)
def create_plan(body: PlanCreate, user: str = Depends(current_user)) -> dict:
    inst = _require_instance(body.instance_id)
    bp = _blueprint(body.blueprint, body.version)
    live = store.live_state.get(body.instance_id)
    if live is None:
        raise HTTPException(409, "no live state for this instance yet; run import first")
    plan = compute_plan(bp, live, target_instance=body.instance_id, agent_installed=(inst["mode"] == "agent" and inst.get("agent_version") is not None), environment=inst["environment"])
    plan["id"] = "plan_" + __import__("uuid").uuid4().hex[:10]
    plan["status"] = "planned"
    plan["approvals"] = []
    store.plans[plan["id"]] = plan
    store.record(user, "plan.created", f"{body.blueprint} v{body.version} → {body.instance_id}", f"{len(plan['changes'])} changes")
    return plan


@app.get("/api/v1/plans/{plan_id}")
def get_plan(plan_id: str, user: str = Depends(current_user)) -> dict:
    plan = store.plans.get(plan_id)
    if not plan:
        raise HTTPException(404)
    return plan


@app.post("/api/v1/plans/{plan_id}/approve")
def approve_plan(plan_id: str, user: str = Depends(current_user)) -> dict:
    plan = store.plans.get(plan_id)
    if not plan:
        raise HTTPException(404)
    if user not in plan["approvals"]:
        plan["approvals"].append(user)
        store.record(user, "plan.approved", plan_id)
    return {"approvals": plan["approvals"], "required": plan["approvals_required"]}


@app.post("/api/v1/plans/{plan_id}/apply")
def apply_plan(plan_id: str, user: str = Depends(current_user)) -> dict:
    plan = store.plans.get(plan_id)
    if not plan:
        raise HTTPException(404)
    if not plan["can_apply"]:
        raise HTTPException(409, plan["blocked_reason"])
    if len(plan["approvals"]) < plan["approvals_required"]:
        raise HTTPException(409, f"{plan['approvals_required']} approvals required, {len(plan['approvals'])} given")
    jobs = [store.enqueue_job(plan["target_instance"], j["kind"], j["params"], {"plan_id": plan_id}) for j in to_agent_job(plan)]
    plan["status"] = "applying"
    store.record(user, "plan.apply_requested", plan_id, ", ".join(j["id"] for j in jobs))
    return {"jobs": [j["id"] for j in jobs]}


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str, user: str = Depends(current_user)) -> dict:
    job = store.jobs.get(job_id)
    if not job:
        raise HTTPException(404)
    return job


@app.get("/api/v1/audit")
def audit(user: str = Depends(current_user)) -> list[dict]:
    return list(reversed(store.audit))[:200]


@app.get("/api/v1/events")
def events(instance_id: Optional[str] = None, kind: Optional[str] = None, limit: int = 200, user: str = Depends(current_user)) -> list[dict]:
    out = [e for e in store.events if (not instance_id or e.get("instance_id") == instance_id) and (not kind or e.get("kind") == kind)]
    return out[-limit:]


# ----------------------------------------------------------------------------- agent transport


class PairBody(BaseModel):
    instance_id: str
    agent_version: str
    report: dict[str, Any] = Field(default_factory=dict)


@app.post("/agent/v1/pair")
def pair(body: PairBody, authorization: Optional[str] = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "pairing token required")
    res = store.pair(authorization[7:], body.agent_version, body.report)
    if not res:
        raise HTTPException(401, "invalid or used pairing token")
    instance_id, token = res
    if instance_id != body.instance_id:
        raise HTTPException(409, "pairing token belongs to another instance")
    store.record("agent:" + instance_id, "agent.paired", instance_id, body.agent_version)
    return {"agent_token": token, "instance_id": instance_id}


class HeartbeatBody(BaseModel):
    agent_version: str
    report: dict[str, Any] = Field(default_factory=dict)


@app.post("/agent/v1/instances/{instance_id}/heartbeat")
def heartbeat(instance_id: str, body: HeartbeatBody, inst: str = Depends(agent_instance)) -> dict:
    if inst != instance_id:
        raise HTTPException(403)
    store.heartbeat(instance_id, body.agent_version, body.report)
    return {"ok": True}


class EventsBody(BaseModel):
    events: list[dict[str, Any]]


@app.post("/agent/v1/instances/{instance_id}/events")
def push_events(instance_id: str, body: EventsBody, inst: str = Depends(agent_instance)) -> dict:
    if inst != instance_id:
        raise HTTPException(403)
    for e in body.events:
        e["instance_id"] = instance_id
        store.events.append(e)
    # Assurance (Slice 3) consumes these; for now they are queryable via /api/v1/events.
    return {"accepted": len(body.events)}


@app.get("/agent/v1/instances/{instance_id}/jobs/next")
def next_job(instance_id: str, inst: str = Depends(agent_instance)) -> Optional[dict]:
    if inst != instance_id:
        raise HTTPException(403)
    return store.next_job(instance_id)


@app.post("/agent/v1/jobs/{job_id}/result")
def job_result(job_id: str, result: dict[str, Any], inst: str = Depends(agent_instance)) -> dict:
    job = store.complete_job(job_id, result, instance_id=inst)
    if not job:  # unknown, or queued for another instance: same answer, nothing written
        raise HTTPException(404)
    store.record("agent:" + inst, f"job.{job['status']}", job_id, job["kind"])
    return {"ok": True}
