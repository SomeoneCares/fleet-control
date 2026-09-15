"""Fleet Control API — Slice 1.

Two route families:

* ``/api/v1/...`` — the web client. Signed-in people only: a session cookie from ``POST /api/v1/auth/login``,
  and every write also carries ``X-Fleet-Control: 1`` (a header a cross-site form cannot send). What each of
  the five roles may do is ``auth.PERMISSIONS``.
* ``/agent/v1/...`` — the fleetctl-agent daemon (pair, heartbeat, events, long-poll jobs, results), with the
  bearer token issued at pairing.

Data lives in the database ``store.py`` opens (FLEETCONTROL_DATABASE_URL; in memory when unset). The first admin comes from FLEETCONTROL_ADMIN_EMAIL (and optionally
FLEETCONTROL_ADMIN_PASSWORD) when nobody exists yet; scripts/dev_api.py does that for local development.

Run: ``uvicorn fleetcontrol_api.main:app --port 8080``
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any, Literal, Optional

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from fleetcontrol_blueprint import Blueprint, dump_blueprint, json_schema, load_blueprint

from .auth import (
    ADMIN_PORTAL, DUMMY_HASH, LOCKOUT_FAILURES, LOCKOUT_SECONDS, ROLES, SESSION_SECONDS, allowed, check_password_policy,
    hash_password, new_password, new_session_token, permissions_for, role_label, token_key, verify_password,
)
from .drift import DriftResolutionError, accept_into_blueprint, live_state_from_drift, select_drift
from .edits import as_new_version, update_agent
from .importer import LiveImportError, blueprint_from_live
from .planner import compute_plan, to_agent_job
from .store import Store

app = FastAPI(title="Fleet Control API", version="0.1.0")
store = Store()

SESSION_COOKIE = "fc_session"
CSRF_HEADER = "X-Fleet-Control"
COOKIE_SECURE = os.environ.get("FLEETCONTROL_COOKIE_SECURE", "0") == "1"  # set to 1 behind HTTPS

RoleName = Literal["admin", "fleet_architect", "operator", "approver", "viewer"]


def _bootstrap_admin() -> None:
    """Create the first admin from the environment when nobody exists yet. There is no default password."""
    email = (os.environ.get("FLEETCONTROL_ADMIN_EMAIL") or "").strip().lower()
    if not email or store.has_users():
        return
    given = os.environ.get("FLEETCONTROL_ADMIN_PASSWORD")
    password = given or new_password()
    check_password_policy(password)
    store.add_user(email, "Administrator", "admin", hash_password(password))
    store.record("fleetcontrol", "user.created", email, "Admin (bootstrap)")
    if not given:
        print(f"Fleet Control: created admin {email} with one-time password {password}; change it after signing in.", flush=True)


_bootstrap_admin()


# ----------------------------------------------------------------------------- people: sessions and roles


def current_user(request: Request, fc_session: Optional[str] = Cookie(default=None)) -> dict:
    user = store.session_user(token_key(fc_session)) if fc_session else None
    if not user:
        raise HTTPException(401, "sign in required")
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, f"writes need the {CSRF_HEADER} header")
    return user


def require(*permissions: str):
    def dependency(user: dict = Depends(current_user)) -> dict:
        missing = [p for p in permissions if not allowed(user["role"], p)]
        if missing:
            raise HTTPException(403, f"the {role_label(user['role'])} role cannot do this ({', '.join(missing)})")
        return user

    return dependency


def _me(user: dict) -> dict:
    return {"email": user["email"], "name": user["name"], "role": user["role"], "role_label": role_label(user["role"]),
            "portal": "admin" if user["role"] in ADMIN_PORTAL else "workspace", "permissions": permissions_for(user["role"])}


def _person(user: dict) -> dict:
    return {**{k: user[k] for k in ("email", "name", "role", "disabled", "created_at", "last_login")}, "role_label": role_label(user["role"])}


class LoginBody(BaseModel):
    email: str
    password: str


@app.post("/api/v1/auth/login")
def login(body: LoginBody, request: Request, response: Response) -> dict:
    if request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, f"sign-in needs the {CSRF_HEADER} header")
    email = body.email.strip().lower()
    if store.recent_failures(email, LOCKOUT_SECONDS) >= LOCKOUT_FAILURES:
        raise HTTPException(429, "too many failed sign-ins for this account; try again in 15 minutes")
    user = store.get_user(email)
    ok = verify_password(body.password, user["password_hash"] if user else DUMMY_HASH)
    if not ok or not user or user["disabled"]:
        store.login_failed(email)
        store.record(email or "unknown", "auth.sign_in_failed", email or "unknown")
        raise HTTPException(401, "email or password is not right")
    store.clear_failures(email)
    token = new_session_token()
    store.create_session(email, token, token_key(token), SESSION_SECONDS)
    user = store.update_user(email, last_login=time.time())
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="lax", secure=COOKIE_SECURE, path="/")
    store.record(email, "auth.signed_in", email)
    return _me(user)


@app.post("/api/v1/auth/logout")
def logout(response: Response, fc_session: Optional[str] = Cookie(default=None), user: dict = Depends(current_user)) -> dict:
    if fc_session:
        store.drop_session(token_key(fc_session))
    response.delete_cookie(SESSION_COOKIE, path="/")
    store.record(user["email"], "auth.signed_out", user["email"])
    return {"ok": True}


@app.get("/api/v1/auth/me")
def me(user: dict = Depends(current_user)) -> dict:
    return _me(user)


class PasswordChange(BaseModel):
    current: str
    new: str


@app.post("/api/v1/auth/password")
def change_password(body: PasswordChange, user: dict = Depends(current_user)) -> dict:
    if not verify_password(body.current, user["password_hash"]):
        raise HTTPException(403, "the current password is not right")
    try:
        check_password_policy(body.new)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    store.update_user(user["email"], password_hash=hash_password(body.new))
    store.record(user["email"], "auth.password_changed", user["email"])
    return {"ok": True}


@app.get("/api/v1/roles")
def roles(user: dict = Depends(current_user)) -> list[dict]:
    members = {r: 0 for r in ROLES}
    for u in store.list_users():
        if not u["disabled"]:
            members[u["role"]] += 1
    return [{"role": r, "label": label, "description": desc, "members": members[r],
             "portal": "admin" if r in ADMIN_PORTAL else "workspace", "permissions": permissions_for(r)}
            for r, (label, desc) in ROLES.items()]


@app.get("/api/v1/users")
def list_users(user: dict = Depends(require("users.read"))) -> list[dict]:
    return [_person(u) for u in store.list_users()]


class UserCreate(BaseModel):
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+$")
    name: str = Field(..., min_length=1)
    role: RoleName


@app.post("/api/v1/users", status_code=201)
def create_user(body: UserCreate, user: dict = Depends(require("users.manage"))) -> dict:
    email = body.email.strip().lower()
    if store.get_user(email):
        raise HTTPException(409, f"{email} already has an account")
    password = new_password()
    created = store.add_user(email, body.name.strip(), body.role, hash_password(password))
    store.record(user["email"], "user.created", email, role_label(body.role))
    return {"user": _person(created), "password": password}  # shown once; only the hash is kept


class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[RoleName] = None
    disabled: Optional[bool] = None


def _active_admins() -> list[dict]:
    return [u for u in store.list_users() if u["role"] == "admin" and not u["disabled"]]


@app.patch("/api/v1/users/{email}")
def update_user(email: str, body: UserUpdate, user: dict = Depends(require("users.manage"))) -> dict:
    target = store.get_user(email.strip().lower())
    if not target:
        raise HTTPException(404, "no such person")
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    demoted = "role" in changes and changes["role"] != "admin"
    if target["email"] == user["email"] and (changes.get("disabled") or demoted):
        raise HTTPException(409, "you cannot disable yourself or remove your own Admin role")
    if target["role"] == "admin" and not target["disabled"] and (changes.get("disabled") or demoted) and len(_active_admins()) <= 1:
        raise HTTPException(409, "Fleet Control needs at least one active Admin")
    if "name" in changes:
        changes["name"] = changes["name"].strip() or target["name"]
    target = store.update_user(target["email"], **changes)
    if changes.get("disabled"):
        store.drop_sessions_for(target["email"])
    detail = ", ".join(f"role={role_label(v)}" if k == "role" else f"{k}={v}" for k, v in changes.items())
    store.record(user["email"], "user.updated", target["email"], detail or "no changes")
    return _person(target)


@app.post("/api/v1/users/{email}/reset-password")
def reset_password(email: str, user: dict = Depends(require("users.manage"))) -> dict:
    target = store.get_user(email.strip().lower())
    if not target:
        raise HTTPException(404, "no such person")
    password = new_password()
    store.update_user(target["email"], password_hash=hash_password(password))
    store.drop_sessions_for(target["email"])
    store.record(user["email"], "user.password_reset", target["email"])
    return {"password": password}


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
def create_instance(body: InstanceCreate, user: dict = Depends(require("instances.connect"))) -> dict:
    if store.get_instance(body.id):
        raise HTTPException(409, "instance exists")
    inst = store.create_instance(body.id, body.environment, user["email"], body.mode)
    store.record(user["email"], "instance.created", body.id, body.mode)
    # The Connect drawer shows this one-liner; the pairing token is single-use.
    inst["install_command"] = (
        f"curl -fsSL https://get.fleetcontrol.example/install.sh | FLEETCONTROL_URL=<this server> "
        f"FLEETCONTROL_INSTANCE_ID={body.id} FLEETCONTROL_PAIRING_TOKEN={inst['pairing_token']} sh"
    )
    return inst


@app.get("/api/v1/instances")
def list_instances(user: dict = Depends(require("instances.read"))) -> list[dict]:
    return [_instance_row(i) for i in store.list_instances()]


def _instance_row(inst: dict) -> dict:
    """An instance as the Instances screen needs it: record + open drift, imported profiles, applied version."""
    iid = inst["id"]
    out = {k: v for k, v in inst.items() if k != "pairing_token"}
    out["open_drift"] = sum(len(d) for d in ((store.drift_report(iid) or {}).get("drift") or {}).values())
    out["live_profile_count"] = len(store.live_state_for(iid) or {})
    out["applied"] = store.applied_for(iid)
    return out


@app.get("/api/v1/instances/{instance_id}")
def get_instance(instance_id: str, user: dict = Depends(require("instances.read"))) -> dict:
    inst = store.get_instance(instance_id)
    if not inst:
        raise HTTPException(404)
    out = _instance_row(inst)
    out["live_profiles"] = list((store.live_state_for(instance_id) or {}).keys())
    out["drift"] = store.drift_report(instance_id)
    return out


@app.post("/api/v1/instances/{instance_id}/import")
def import_profiles(instance_id: str, user: dict = Depends(require("instances.operate"))) -> dict:
    _require_instance(instance_id)
    job = store.enqueue_job(instance_id, "import_profiles", {})
    store.record(user["email"], "instance.import_requested", instance_id, job["id"])
    return {"job_id": job["id"]}


@app.post("/api/v1/instances/{instance_id}/drift-scan")
def drift_scan(instance_id: str, blueprint: str, version: int, user: dict = Depends(require("instances.operate"))) -> dict:
    _require_instance(instance_id)
    bp = _blueprint(blueprint, version)
    job = store.enqueue_job(instance_id, "drift_scan", {"managed": bp.managed_fields()}, {"blueprint": blueprint, "version": version})
    store.record(user["email"], "drift.scan_requested", instance_id, f"{blueprint} v{version}")
    return {"job_id": job["id"]}


def _require_instance(instance_id: str) -> dict:
    inst = store.get_instance(instance_id)
    if not inst:
        raise HTTPException(404, "unknown instance")
    return inst


# ----------------------------------------------------------------------------- web: drift


@app.get("/api/v1/instances/{instance_id}/drift")
def get_drift(instance_id: str, user: dict = Depends(require("drift.read"))) -> dict:
    _require_instance(instance_id)
    report = store.drift_report(instance_id)
    if not report:
        raise HTTPException(404, "no drift scan for this instance yet")
    return {**report, "exceptions": store.active_exceptions(instance_id)}


class DriftField(BaseModel):
    profile: str
    field: str


class DriftResolve(BaseModel):
    action: Literal["accept", "revert", "ignore_once", "exception"]
    fields: list[DriftField] = Field(default_factory=list, description="Fields to resolve; empty means every open one.")
    expires_at: Optional[float] = Field(None, description="Unix time the exception ends; required for 'exception'.")
    reason: Optional[str] = None


@app.post("/api/v1/instances/{instance_id}/drift/resolve")
def resolve_drift(instance_id: str, body: DriftResolve, user: dict = Depends(require("drift.resolve"))) -> dict:
    inst = _require_instance(instance_id)
    if body.action == "accept" and not allowed(user["role"], "blueprints.write"):
        raise HTTPException(403, f"accepting drift writes a blueprint version; the {role_label(user['role'])} role cannot")
    report = store.drift_report(instance_id)
    if not report or not report.get("drift"):
        raise HTTPException(409, "no open drift on this instance")
    try:
        chosen = select_drift(report["drift"], [f.model_dump() for f in body.fields])
    except DriftResolutionError as exc:
        raise HTTPException(422, str(exc))
    who = user["email"]
    target = f"{instance_id}: " + ", ".join(f"{p}.{d['field']}" for p, diffs in chosen.items() for d in diffs)

    if body.action == "ignore_once":
        store.move_drift(instance_id, chosen, "ignored")
        store.record(who, "drift.ignored_once", target)
        return {"action": body.action, "resolved": chosen}

    if body.action == "exception":
        if body.expires_at is None or body.expires_at <= time.time():
            raise HTTPException(422, "an exception needs an expires_at in the future")
        store.add_exceptions(instance_id, chosen, body.expires_at, who, body.reason)
        until = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(body.expires_at))
        store.record(who, "drift.exception_created", target, f"until {until}" + (f"; {body.reason}" if body.reason else ""))
        return {"action": body.action, "resolved": chosen, "expires_at": body.expires_at}

    # accept and revert work against the blueprint version the scan compared with
    if not report.get("blueprint"):
        raise HTTPException(409, "this drift report does not name a blueprint version; run a drift scan again")
    bp = _blueprint(report["blueprint"], report["version"])

    if body.action == "revert":
        plan = _save_plan(bp, live_state_from_drift(bp, chosen), inst, who, why=" (revert drift)")
        store.record(who, "drift.revert_planned", target, plan["id"])
        return {"action": body.action, "resolved": chosen, "plan": plan}

    new_version = store.next_blueprint_version(bp.metadata.name)
    soul_texts = {p: s["soul_text"] for p, s in (store.live_state_for(instance_id) or {}).items() if isinstance(s.get("soul_text"), str)}
    try:
        new_bp = accept_into_blueprint(bp, chosen, new_version=new_version, soul_texts=soul_texts)
    except DriftResolutionError as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:  # the live values do not make a valid blueprint (e.g. no model provider)
        raise HTTPException(422, f"live values do not form a valid blueprint: {exc}")
    rec = store.save_blueprint(new_bp.metadata.name, new_version, dump_blueprint(new_bp), new_bp.model_dump(mode="json"), who)
    store.move_drift(instance_id, chosen, "accepted")
    store.record(who, "drift.accepted", target, f"{new_bp.metadata.name} v{new_version} (draft)")
    return {"action": body.action, "resolved": chosen, "blueprint": rec["name"], "version": rec["version"], "status": rec["status"]}


# ----------------------------------------------------------------------------- web: blueprints


class BlueprintUpload(BaseModel):
    yaml: str


@app.get("/api/v1/blueprints/schema")
def blueprint_schema(user: dict = Depends(require("blueprints.read"))) -> dict:
    return json_schema()


@app.post("/api/v1/blueprints", status_code=201)
def upload_blueprint(body: BlueprintUpload, user: dict = Depends(require("blueprints.write"))) -> dict:
    try:
        bp = load_blueprint(body.yaml)
    except Exception as exc:
        raise HTTPException(422, f"invalid blueprint: {exc}")
    existing = store.get_blueprint(bp.metadata.name, bp.metadata.version)
    if existing and existing["status"] != "draft":
        raise HTTPException(409, f"{bp.metadata.name} v{bp.metadata.version} is {existing['status']} and immutable; "
                                 "raise metadata.version to upload a new version")
    rec = store.save_blueprint(bp.metadata.name, bp.metadata.version, dump_blueprint(bp), bp.model_dump(mode="json"), user["email"])
    store.record(user["email"], "blueprint.saved", f"{bp.metadata.name} v{bp.metadata.version}")
    return {"name": rec["name"], "version": rec["version"], "status": rec["status"], "managed_profiles": list(bp.managed_fields())}


@app.get("/api/v1/blueprints")
def list_blueprints(user: dict = Depends(require("blueprints.read"))) -> list[dict]:
    out, applied = [], store.all_applied()
    for name, versions in store.all_blueprints().items():
        latest = versions[max(versions)]
        parsed = latest["parsed"]
        out.append({
            "name": name, "versions": sorted(versions), "latest": max(versions), "status": latest["status"],
            "owner": parsed["metadata"]["owner"], "description": parsed["metadata"].get("description"),
            "agents": len(parsed["agents"]), "workflows": len(parsed.get("workflows") or []),
            "tests": len(parsed.get("tests") or []), "updated_at": latest["created_at"], "author": latest["author"],
            "applied_on": [{"instance": iid, "version": a["version"], "at": a["at"]}
                           for iid, a in applied.items() if a["name"] == name],
        })
    return out


@app.get("/api/v1/blueprints/{name}")
def blueprint_versions(name: str, user: dict = Depends(require("blueprints.read"))) -> list[dict]:
    versions = store.blueprint_versions(name)
    if not versions:
        raise HTTPException(404)
    return [{"version": v, "status": r["status"], "author": r["author"], "created_at": r["created_at"]}
            for v, r in sorted(versions.items(), reverse=True)]


@app.get("/api/v1/blueprints/{name}/{version}")
def get_blueprint(name: str, version: int, user: dict = Depends(require("blueprints.read"))) -> dict:
    rec = store.get_blueprint(name, version)
    if not rec:
        raise HTTPException(404)
    return {"name": name, "version": version, "status": rec["status"], "yaml": rec["yaml"], "parsed": rec["parsed"],
            "managed": Blueprint.model_validate(rec["parsed"]).managed_fields(), "author": rec["author"],
            "created_at": rec["created_at"]}


@app.post("/api/v1/blueprints/{name}/{version}/draft", status_code=201)
def create_draft(name: str, version: int, user: dict = Depends(require("blueprints.write"))) -> dict:
    """Start a new draft from any version (applied versions are immutable)."""
    bp = _blueprint(name, version)
    new_version = store.next_blueprint_version(name)
    new = as_new_version(bp, new_version)
    rec = store.save_blueprint(name, new_version, dump_blueprint(new), new.model_dump(mode="json"), user["email"])
    store.record(user["email"], "blueprint.draft_created", f"{name} v{new_version}", f"from v{version}")
    return {"name": name, "version": new_version, "status": rec["status"]}


class LiveImport(BaseModel):
    name: str
    profiles: Optional[list[str]] = Field(None, description="Profiles to include; default every imported one.")


@app.post("/api/v1/instances/{instance_id}/blueprint-from-live", status_code=201)
def blueprint_from_instance(instance_id: str, body: LiveImport, user: dict = Depends(require("blueprints.write"))) -> dict:
    """A Blueprint v1 draft describing what the instance runs today (from its last import)."""
    inst = _require_instance(instance_id)
    live = store.live_state_for(instance_id)
    if not live:
        raise HTTPException(409, "no live profiles for this instance yet; run an import first")
    if store.blueprint_versions(body.name):
        raise HTTPException(409, f"a blueprint named {body.name!r} already exists; choose another name")
    try:
        bp, skipped = blueprint_from_live(live, name=body.name, owner=user["email"], instance_id=instance_id,
                                          environment=inst["environment"], profiles=body.profiles)
    except LiveImportError as exc:
        raise HTTPException(422, str(exc))
    except ValueError as exc:  # e.g. a name outside the id rules
        raise HTTPException(422, f"invalid blueprint: {exc}")
    rec = store.save_blueprint(bp.metadata.name, 1, dump_blueprint(bp), bp.model_dump(mode="json"), user["email"])
    store.record(user["email"], "blueprint.imported", f"{bp.metadata.name} v1",
                 f"from {instance_id}: {len(bp.agents)} agents" + (f", {len(skipped)} skipped" if skipped else ""))
    return {"name": rec["name"], "version": rec["version"], "status": rec["status"],
            "managed_profiles": list(bp.managed_fields()), "skipped": skipped}


class AgentEdit(BaseModel):
    role: Optional[str] = None
    model: Optional[dict[str, Any]] = None
    soul: Optional[dict[str, Any]] = None
    skills: Optional[list[str]] = None
    toolsets: Optional[list[str]] = None
    mcps: Optional[list[str]] = None
    delegates_to: Optional[list[str]] = None
    content_zones: Optional[list[str]] = None
    tests: Optional[list[str]] = None


@app.put("/api/v1/blueprints/{name}/{version}/agents/{agent_id}")
def edit_agent(name: str, version: int, agent_id: str, body: AgentEdit, user: dict = Depends(require("blueprints.write"))) -> dict:
    rec = store.get_blueprint(name, version)
    if not rec:
        raise HTTPException(404, "unknown blueprint version")
    if rec["status"] != "draft":
        raise HTTPException(409, f"{name} v{version} is {rec['status']} and immutable; create a draft to edit it")
    try:
        new, changed = update_agent(Blueprint.model_validate(rec["parsed"]), agent_id, body.model_dump(exclude_unset=True))
    except KeyError:
        raise HTTPException(404, f"no agent {agent_id!r} in {name} v{version}")
    except ValueError as exc:  # EditError, or the edit makes the blueprint invalid
        raise HTTPException(422, str(exc))
    store.update_blueprint(name, version, dump_blueprint(new), new.model_dump(mode="json"), user["email"])
    store.record(user["email"], "blueprint.edited", f"{name} v{version}", f"{agent_id}: {', '.join(changed) if changed else 'no changes'}")
    agent = new.agent(agent_id)
    return {"name": name, "version": version, "status": rec["status"], "changed": changed,
            "agent": agent.model_dump(mode="json"), "managed": new.managed_fields()[agent.profile_name]}


def _blueprint(name: str, version: int) -> Blueprint:
    rec = store.get_blueprint(name, version)
    if not rec:
        raise HTTPException(404, "unknown blueprint version")
    return Blueprint.model_validate(rec["parsed"])


# ----------------------------------------------------------------------------- web: plans


class PlanCreate(BaseModel):
    blueprint: str
    version: int
    instance_id: str


@app.post("/api/v1/plans", status_code=201)
def create_plan(body: PlanCreate, user: dict = Depends(require("plans.create"))) -> dict:
    inst = _require_instance(body.instance_id)
    bp = _blueprint(body.blueprint, body.version)
    live = store.live_state_for(body.instance_id)
    if live is None:
        raise HTTPException(409, "no live state for this instance yet; run import first")
    return _save_plan(bp, live, inst, user["email"])


def _save_plan(bp: Blueprint, live: dict, inst: dict, who: str, why: str = "") -> dict:
    plan = compute_plan(bp, live, target_instance=inst["id"], agent_installed=(inst["mode"] == "agent" and inst.get("agent_version") is not None), environment=inst["environment"])
    plan["id"] = "plan_" + uuid.uuid4().hex[:10]
    plan["status"] = "planned"
    plan["approvals"] = []
    plan["created_by"] = who
    plan["created_at"] = time.time()
    store.save_plan(plan)
    store.record(who, "plan.created", f"{bp.metadata.name} v{bp.metadata.version} → {inst['id']}", f"{len(plan['changes'])} changes{why}")
    return plan


@app.get("/api/v1/plans")
def list_plans(status: Optional[str] = None, user: dict = Depends(require("plans.read"))) -> list[dict]:
    rows = [{**{k: p[k] for k in ("id", "target_instance", "environment", "blueprint", "status", "approvals",
                                  "approvals_required", "created_by", "created_at")},
             "changes": sum(1 for r in p["changes"] if r["kind"] != "approval")}
            for p in store.list_plans(status)]
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)


@app.get("/api/v1/plans/{plan_id}")
def get_plan(plan_id: str, user: dict = Depends(require("plans.read"))) -> dict:
    plan = store.get_plan(plan_id)
    if not plan:
        raise HTTPException(404)
    return plan


@app.post("/api/v1/plans/{plan_id}/approve")
def approve_plan(plan_id: str, user: dict = Depends(current_user)) -> dict:
    plan = store.get_plan(plan_id)
    if not plan:
        raise HTTPException(404)
    if plan["status"] != "planned":
        raise HTTPException(409, f"this plan is {plan['status']}; only a plan that has not been applied can be approved")
    production = plan["environment"] == "production"
    if not allowed(user["role"], "plans.approve.production" if production else "plans.approve.nonprod"):
        raise HTTPException(403, f"the {role_label(user['role'])} role cannot approve {plan['environment']} plans")
    if plan.get("created_by") == user["email"]:
        raise HTTPException(403, "you created this plan; another person has to approve it")
    if user["email"] not in plan["approvals"]:
        plan = store.add_approval(plan_id, user["email"])
        store.record(user["email"], "plan.approved", plan_id, f"{len(plan['approvals'])} of {plan['approvals_required']}")
    return {"approvals": plan["approvals"], "required": plan["approvals_required"]}


@app.post("/api/v1/plans/{plan_id}/apply")
def apply_plan(plan_id: str, user: dict = Depends(current_user)) -> dict:
    plan = store.get_plan(plan_id)
    if not plan:
        raise HTTPException(404)
    production = plan["environment"] == "production"
    if not allowed(user["role"], "plans.apply.production" if production else "plans.apply.nonprod"):
        raise HTTPException(403, f"the {role_label(user['role'])} role cannot apply {plan['environment']} plans")
    if plan["status"] != "planned":
        raise HTTPException(409, f"this plan is already {plan['status']}")
    if not plan["can_apply"]:
        raise HTTPException(409, plan["blocked_reason"])
    if len(plan["approvals"]) < plan["approvals_required"]:
        raise HTTPException(409, f"{plan['approvals_required']} approvals required, {len(plan['approvals'])} given")
    if not store.transition_plan(plan_id, "planned", "applying"):  # two people pressed Apply at once: one wins
        raise HTTPException(409, "this plan is already being applied")
    jobs = [store.enqueue_job(plan["target_instance"], j["kind"], j["params"], {"plan_id": plan_id}) for j in to_agent_job(plan)]
    store.record(user["email"], "plan.apply_requested", plan_id, ", ".join(j["id"] for j in jobs))
    return {"jobs": [j["id"] for j in jobs]}


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str, user: dict = Depends(require("instances.read"))) -> dict:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404)
    return job


@app.get("/api/v1/audit")
def audit(limit: int = 200, user: dict = Depends(require("audit.read"))) -> list[dict]:
    return store.audit_log(max(1, min(limit, 5000)))


@app.get("/api/v1/audit/export")
def audit_export(user: dict = Depends(require("audit.read"))) -> Response:
    """The whole audit log as CSV (build document §7: export is CSV in v1)."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time_utc", "actor", "action", "target", "detail"])
    entries = store.audit_log(newest_first=False)
    for e in entries:
        w.writerow([time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e["ts"])), e["actor"], e["action"], e["target"], e["detail"]])
    store.record(user["email"], "audit.exported", "audit log", f"{len(entries)} entries")
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="fleetcontrol-audit.csv"'})


@app.get("/api/v1/events")
def events(instance_id: Optional[str] = None, kind: Optional[str] = None, limit: int = 200,
           user: dict = Depends(require("instances.read"))) -> list[dict]:
    return store.list_events(instance_id, kind, max(1, min(limit, 5000)))


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
    store.add_events(instance_id, body.events)
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
    applied = store.applied_for(inst) if job["kind"] == "apply" and job["status"] == "done" else None
    if applied:
        # drift detection starts right after an apply, against the version just applied
        bp = _blueprint(applied["name"], applied["version"])
        scan = store.enqueue_job(inst, "drift_scan", {"managed": bp.managed_fields()}, {"blueprint": applied["name"], "version": applied["version"]})
        store.record("fleetcontrol", "drift.scan_requested", inst, f"{applied['name']} v{applied['version']} after apply ({scan['id']})")
    return {"ok": True}
