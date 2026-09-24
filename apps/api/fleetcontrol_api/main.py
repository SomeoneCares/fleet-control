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
from typing import Any, Iterable, Literal, Optional

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError

from fleetcontrol_blueprint import Blueprint, dump_blueprint, json_schema, load_blueprint

from .workflows import (
    WorkflowError, agents_used, compose_input, current_step, decide_gate, escalate_due, finish_room_step, kanban_outcome,
    kanban_tasks, segment,
    instructions as wf_instructions, may_decide_gate, missing_requirements, new_run, normalize_steps, pending_members,
    question_for, record_result, run_row as wf_run_row, settle, start_step,
)
from .access import (
    agent_grants, combined_zones, person_permissions, person_zones, summary as access_summary, tool_verdict, why_apply, why_room,
)
from .notifications import (
    PERSONAL_EVENTS, NotificationError, effective as notify_effective, reach as notify_reach, update as notify_update,
)
from .messaging import (
    EVENTS, TEMPLATES, MessagingError, channel_health, merge_gateway, new_channel, new_delivery, render, rules_from, test_text,
)
from .ask import (
    ORCHESTRATOR_BLUEPRINT, AskError, orchestrator_blueprint, check_question, classification_of, granted_zones, grounding, instructions as ask_instructions, parse_answer,
    request_text as ask_request_text, room_text, save_targets, select_sources, thread_row,
)
from .architect import (
    ARCHITECT_BLUEPRINT, EDITABLE, Constraints, ProposalError, architect_blueprint, check_agent, estate_summary, instructions,
    merge_edit, parse_proposal, request_text, to_blueprint,
)
from .content import (
    CLASSIFICATIONS, ContentError, agent_access, check_classification, file_row, may_read, new_file, new_zone, readable_zones,
)
from .auth import (
    ADMIN_PORTAL, DUMMY_HASH, LOCKOUT_FAILURES, LOCKOUT_SECONDS, ROLES, SESSION_SECONDS, allowed, check_password_policy,
    hash_password, new_password, new_session_token, permissions_for, role_label, token_key, verify_password,
)
from .drift import DriftResolutionError, accept_into_blueprint, live_state_from_drift, select_drift
from .edits import as_new_version, remove_test, update_agent, upsert_test
from .testlab import VERDICTS, evaluate
from .importer import LiveImportError, blueprint_from_live
from .integrations import aggregate, mcp_config
from .outputs import KINDS, OutputError, new_output, output_row, provenance
from .rooms import RoomError, check_tool, decide, new_evidence, new_finding, new_room, room_row, room_view
from .planner import compute_plan, mcp_sources, to_agent_job
from .settings import DEFAULTS as SETTING_DEFAULTS
from .settings import SettingsUpdate, approval_floor, effective as effective_settings
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


def _settings() -> dict:
    return effective_settings(store.get_settings())


def current_user(request: Request, fc_session: Optional[str] = Cookie(default=None),
                 authorization: Optional[str] = Header(default=None)) -> dict:
    if authorization and authorization.startswith("Bearer fct_"):
        # An API token (Settings → API tokens) acts as its owner. It is not a cookie, so a cross-site page
        # cannot make the browser send it, and writes need no X-Fleet-Control header.
        found = store.api_token_user(authorization[7:])
        if not found:
            raise HTTPException(401, "API token unknown, expired or revoked")
        user, token = found
        return {**user, "via_token": token["id"]}
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
            "portal": "admin" if user["role"] in ADMIN_PORTAL else "workspace", "permissions": permissions_for(user["role"]),
            "workspace_name": _settings()["workspace_name"], "features": {"messaging": bool(_settings()["messaging_enabled"])}}


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
    seconds = int(_settings()["session_hours"]) * 3600  # Settings → General
    store.create_session(email, token, token_key(token), seconds)
    user = store.update_user(email, last_login=time.time())
    response.set_cookie(SESSION_COOKIE, token, max_age=seconds, httponly=True, samesite="lax", secure=COOKIE_SECURE, path="/")
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
    _not_by_token(user, "change a password")
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


# ----------------------------------------------------------------------------- settings and API tokens


@app.get("/api/v1/settings")
def get_settings(user: dict = Depends(require("settings.read"))) -> dict:
    return {"values": _settings(), "defaults": SETTING_DEFAULTS, "updated": store.settings_updated()}


@app.patch("/api/v1/settings")
def update_settings(body: SettingsUpdate, user: dict = Depends(require("settings.manage"))) -> dict:
    before = _settings()
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None and v != before[k]}
    if changes:
        store.set_settings(changes, user["email"])
        store.record(user["email"], "settings.updated", "settings", ", ".join(f"{k}: {before[k]} → {v}" for k, v in changes.items()))
    return {"values": _settings(), "defaults": SETTING_DEFAULTS, "updated": store.settings_updated()}


def _not_by_token(user: dict, what: str) -> None:
    """A leaked token must not be able to mint more access or lock its owner out."""
    if user.get("via_token"):
        raise HTTPException(403, f"an API token cannot {what}; sign in to do that")


class TokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    expires_days: int = Field(30, ge=1)


@app.get("/api/v1/tokens")
def list_tokens(all_people: bool = False, user: dict = Depends(current_user)) -> list[dict]:
    if all_people and not allowed(user["role"], "users.manage"):
        raise HTTPException(403, "only an Admin can see everyone's tokens")
    return store.list_api_tokens(None if all_people else user["email"])


@app.post("/api/v1/tokens", status_code=201)
def create_token(body: TokenCreate, user: dict = Depends(current_user)) -> dict:
    _not_by_token(user, "create API tokens")
    limit = int(_settings()["token_max_days"])
    if body.expires_days > limit:
        raise HTTPException(422, f"a token may last at most {limit} days (Settings → General)")
    rec, secret = store.create_api_token(user["email"], body.name.strip(), time.time() + body.expires_days * 86400)
    until = time.strftime("%Y-%m-%d", time.gmtime(rec["expires_at"]))
    store.record(user["email"], "token.created", rec["id"], f"{rec['name']}, expires {until}")
    return {"token": rec, "secret": secret}  # the secret is shown once; only its hash is kept


@app.delete("/api/v1/tokens/{token_id}")
def revoke_token(token_id: str, user: dict = Depends(current_user)) -> dict:
    tok = store.get_api_token(token_id)
    if not tok or (tok["owner"] != user["email"] and not allowed(user["role"], "users.manage")):
        raise HTTPException(404, "no such token")
    rec = store.revoke_api_token(token_id)
    store.record(user["email"], "token.revoked", token_id, tok["name"] + ("" if tok["owner"] == user["email"] else f" (of {tok['owner']})"))
    return rec


# ----------------------------------------------------------------------------- Content zones (Slice 4)


def _blueprint_docs() -> list[dict]:
    return [versions[max(versions)]["parsed"] for versions in store.all_blueprints().values()]


def _zone_row(zone: dict, counts: dict[str, int], blueprints: list[dict], user: dict) -> dict:
    return {**zone, "files": counts.get(zone["id"], 0), "agents": agent_access(zone["id"], blueprints),
            "may_read": may_read(zone, user["role"])}


@app.get("/api/v1/content/zones")
def list_zones(user: dict = Depends(require("content.read"))) -> list[dict]:
    """Every zone, with how many files it holds and which agents read it. ``may_read`` says whether this person does."""
    counts, blueprints = store.count_files(), _blueprint_docs()
    return [_zone_row(z, counts, blueprints, user) for z in store.list_zones()]


class ZoneCreate(BaseModel):
    id: str
    name: str = Field(..., min_length=1, max_length=80)
    description: str = ""
    read_roles: list[RoleName] = Field(default_factory=list)


@app.post("/api/v1/content/zones", status_code=201)
def create_zone(body: ZoneCreate, user: dict = Depends(require("content.manage"))) -> dict:
    try:
        zone = new_zone(id=body.id, name=body.name, description=body.description, read_roles=list(body.read_roles),
                        by=user["email"], at=time.time())
    except ContentError as exc:
        raise HTTPException(422, str(exc))
    if store.get_zone(zone["id"]):
        raise HTTPException(409, f"a zone {zone['id']!r} already exists")
    store.save_zone(zone)
    store.record(user["email"], "content.zone_created", zone["id"], ", ".join(zone["read_roles"]) or "no role but Admin")
    return _zone_row(zone, store.count_files(), _blueprint_docs(), user)


class ZoneEdit(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    description: Optional[str] = None
    read_roles: Optional[list[RoleName]] = None


@app.patch("/api/v1/content/zones/{zone}")
def edit_zone(zone: str, body: ZoneEdit, user: dict = Depends(require("content.manage"))) -> dict:
    current = store.get_zone(zone)
    if not current:
        raise HTTPException(404, "no such zone")
    if current.get("managed"):
        raise HTTPException(409, f"{zone} is synced from {current.get('source') or 'an external source'}; edit it there")
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "read_roles" in changes:
        changes["read_roles"] = sorted(set(changes["read_roles"]))
    updated = store.save_zone({**current, **changes})
    store.record(user["email"], "content.zone_updated", zone,
                 ", ".join(f"{k}={v if not isinstance(v, list) else ', '.join(v) or 'none'}" for k, v in changes.items()) or "no changes")
    return _zone_row(updated, store.count_files(), _blueprint_docs(), user)


@app.delete("/api/v1/content/zones/{zone}")
def delete_zone(zone: str, user: dict = Depends(require("content.manage"))) -> dict:
    current = store.get_zone(zone)
    if not current:
        raise HTTPException(404, "no such zone")
    if store.count_files().get(zone):
        raise HTTPException(409, f"{zone} still holds files; remove them first")
    store.delete_zone(zone)
    store.record(user["email"], "content.zone_deleted", zone)
    return {"ok": True}


def _my_zones(user: dict) -> list[str]:
    return readable_zones(store.list_zones(), user["role"])


@app.get("/api/v1/content/files")
def list_content_files(zone: Optional[str] = None, user: dict = Depends(require("content.read"))) -> list[dict]:
    """Files in the zones this person may read (an unreadable zone answers empty, not 403: it is not theirs to know)."""
    mine = _my_zones(user)
    zones = [zone] if zone and zone in mine else [] if zone else mine
    return [file_row(f) for f in store.list_files(zones)]


class FileUpload(BaseModel):
    zone: str
    name: str = Field(..., min_length=1, max_length=200)
    classification: str = "internal"
    text: Optional[str] = None


@app.post("/api/v1/content/files", status_code=201)
def upload_content_file(body: FileUpload, user: dict = Depends(require("content.manage"))) -> dict:
    zone = store.get_zone(body.zone)
    if not zone:
        raise HTTPException(404, "no such zone")
    if zone.get("managed"):
        raise HTTPException(409, f"{zone['id']} is synced from {zone.get('source') or 'an external source'}; add the file there")
    try:
        doc = new_file(zone=zone["id"], name=body.name, classification=check_classification(body.classification), text=body.text,
                       by=user["email"], at=time.time(), file_id="cf_" + uuid.uuid4().hex[:10])
    except ContentError as exc:
        raise HTTPException(422, str(exc))
    store.save_file(doc)
    store.record(user["email"], "content.file_added", f"{zone['id']}/{doc['name']}", doc["classification"])
    return file_row(doc, with_text=False)


@app.get("/api/v1/content/files/{file_id}")
def get_content_file(file_id: str, user: dict = Depends(require("content.read"))) -> dict:
    doc = store.get_file(file_id)
    if not doc or doc["zone"] not in _my_zones(user):
        raise HTTPException(404, "no such file")
    return file_row(doc, with_text=True)


@app.delete("/api/v1/content/files/{file_id}")
def delete_content_file(file_id: str, user: dict = Depends(require("content.manage"))) -> dict:
    doc = store.get_file(file_id)
    if not doc:
        raise HTTPException(404, "no such file")
    store.delete_file(file_id)
    store.record(user["email"], "content.file_removed", f"{doc['zone']}/{doc['name']}")
    return {"ok": True}


@app.get("/api/v1/content/classifications")
def classifications(user: dict = Depends(require("content.read"))) -> list[str]:
    return list(CLASSIFICATIONS)


# ----------------------------------------------------------------------------- Fleet outputs (Slice 4)


def _output_out(o: dict, *, with_text: bool = False) -> dict:
    return {**output_row(o, with_text=with_text), "provenance": provenance(o)}


@app.get("/api/v1/outputs")
def list_outputs(zone: Optional[str] = None, case: Optional[str] = None, limit: int = 100,
                 user: dict = Depends(require("content.read"))) -> list[dict]:
    """What the fleet produced in the zones this person may read."""
    mine = _my_zones(user)
    zones = [zone] if zone and zone in mine else [] if zone else mine
    return [_output_out(o) for o in store.list_outputs(zones, case=case, limit=max(1, min(limit, 500)))]


@app.get("/api/v1/outputs/{output_id}")
def get_output(output_id: str, user: dict = Depends(require("content.read"))) -> dict:
    o = store.get_output(output_id)
    if not o or o["zone"] not in _my_zones(user):
        raise HTTPException(404, "no such output")
    return _output_out(o, with_text=True)


class OutputCreate(BaseModel):
    zone: str
    name: str = Field(..., min_length=1, max_length=200)
    kind: Literal["document", "structured", "graph", "markdown", "summary"] = "markdown"
    classification: str = "internal"
    text: Optional[str] = None
    case: Optional[str] = None
    source: Optional[dict[str, Any]] = None


def _save_output(body: OutputCreate, *, produced_by: str, source: dict, instance_id: Optional[str] = None,
                 actor: str, action: str) -> dict:
    zone = store.get_zone(body.zone)
    if not zone:
        raise HTTPException(404, "no such zone")
    try:
        doc = new_output(output_id="out_" + uuid.uuid4().hex[:10], zone=zone["id"], name=body.name, kind=body.kind,
                         classification=body.classification, produced_by=produced_by, at=time.time(), text=body.text,
                         case=body.case, source=source, instance_id=instance_id)
    except (OutputError, ContentError) as exc:
        raise HTTPException(422, str(exc))
    store.save_output(doc)
    store.record(actor, action, f"{zone['id']}/{doc['name']}", f"{doc['classification']}, by {produced_by}")
    _output_event(doc)
    return _output_out(doc)


def _output_event(doc: dict) -> None:
    _notify("output.shared", key=f"output:{doc['id']}",
            ctx={"title": doc["name"], "case": doc.get("case"), "instance": doc.get("instance_id"),
                 "detail": f"{doc['classification']}, in zone {doc['zone']}, by {doc['produced_by']}.", "link": f"/outputs?id={doc['id']}"})


@app.post("/api/v1/outputs", status_code=201)
def create_output(body: OutputCreate, user: dict = Depends(require("content.manage"))) -> dict:
    """A person filing something into a zone; the fleet's own outputs arrive through the agent route."""
    source = body.source or {"kind": "upload"}
    return _save_output(body, produced_by=user["email"], source=source, actor=user["email"], action="output.saved")


@app.delete("/api/v1/outputs/{output_id}")
def delete_output(output_id: str, user: dict = Depends(require("content.manage"))) -> dict:
    o = store.get_output(output_id)
    if not o:
        raise HTTPException(404, "no such output")
    store.delete_output(output_id)
    store.record(user["email"], "output.removed", f"{o['zone']}/{o['name']}")
    return {"ok": True}


@app.get("/api/v1/outputs-kinds")
def output_kinds(user: dict = Depends(require("content.read"))) -> list[str]:
    return list(KINDS)


# ----------------------------------------------------------------------------- Decision Rooms (Slice 4)


def _room(room_id: str, user: dict) -> dict:
    """A room the person may see: the zone decides, so an unreadable room is simply not there."""
    room = store.get_room(room_id)
    if not room or room["zone"] not in _my_zones(user):
        raise HTTPException(404, "no such decision room")
    return room


class RoomCreate(BaseModel):
    question: str = Field(..., min_length=10, max_length=300)
    zone: str
    options: list[str] = Field(..., min_length=2, max_length=6)
    case: Optional[str] = None
    due_at: Optional[float] = None
    second_approver: Optional[str] = None


def _open_room(body: RoomCreate, *, opened_by: str, kind: str, actor: str, instance_id: Optional[str] = None) -> dict:
    zone = store.get_zone(body.zone)
    if not zone:
        raise HTTPException(404, "no such content zone")
    if body.second_approver and not store.get_user(body.second_approver.strip().lower()):
        raise HTTPException(422, f"no account for the second approver {body.second_approver!r}")
    try:
        room = new_room(room_id="room_" + uuid.uuid4().hex[:10], question=body.question, zone=zone["id"], options=body.options,
                        opened_by=opened_by, opened_by_kind=kind, at=time.time(), case=body.case, due_at=body.due_at,
                        second_approver=body.second_approver, instance_id=instance_id)
    except RoomError as exc:
        raise HTTPException(422, str(exc))
    store.save_room(room)
    store.record(actor, "room.opened", room["id"], f"{room['question'][:80]} ({zone['id']})")
    _notify("decision_room.opened", key=f"room:{room['id']}:opened",
            ctx={"title": room["question"], "case": room.get("case"), "instance": instance_id, "link": f"/rooms/{room['id']}"})
    _notify_people("room.waiting", key=f"room:{room['id']}:waiting",
                   ctx={"title": room["question"], "case": room.get("case"), "link": f"/rooms/{room['id']}"},
                   people=_people_with("rooms.decide", zone=room["zone"], except_=[opened_by]))
    return room


@app.get("/api/v1/rooms")
def list_rooms(status: Optional[str] = None, case: Optional[str] = None, mine: bool = False,
               user: dict = Depends(require("rooms.read"))) -> list[dict]:
    """Rooms in the zones this person may read. ``mine`` keeps the ones still waiting for their decision."""
    rooms = store.list_rooms(_my_zones(user), status=status, case=case)
    rows = [room_row(r, email=user["email"], role=user["role"]) for r in rooms]
    return [r for r in rows if r["may_decide"]] if mine else rows


@app.post("/api/v1/rooms", status_code=201)
def open_room(body: RoomCreate, user: dict = Depends(require("rooms.open"))) -> dict:
    if body.zone not in _my_zones(user):  # nobody opens a room into a zone they could not then read
        raise HTTPException(404, "no such content zone")
    room = _open_room(body, opened_by=user["email"], kind="person", actor=user["email"])
    return room_view(room, email=user["email"], role=user["role"])


@app.get("/api/v1/rooms/{room_id}")
def get_room(room_id: str, user: dict = Depends(require("rooms.read"))) -> dict:
    return room_view(_room(room_id, user), email=user["email"], role=user["role"])


class EvidenceAdd(BaseModel):
    kind: Literal["file", "output", "claim", "note"]
    label: str = Field(..., min_length=1, max_length=300)
    ref: Optional[str] = None
    source: Optional[str] = None
    verdict: Optional[str] = None
    basis: Optional[Literal["source", "analytical", "interpretation", "assumption", "judgment"]] = None


@app.post("/api/v1/rooms/{room_id}/evidence", status_code=201)
def add_evidence(room_id: str, body: EvidenceAdd, user: dict = Depends(require("rooms.open"))) -> dict:
    room = _room(room_id, user)
    if body.kind == "file" and body.ref and (store.get_file(body.ref) or {}).get("zone") not in _my_zones(user):
        raise HTTPException(404, "no such file in a zone you may read")
    if body.kind == "output" and body.ref and (store.get_output(body.ref) or {}).get("zone") not in _my_zones(user):
        raise HTTPException(404, "no such output in a zone you may read")
    try:
        item = new_evidence(kind=body.kind, label=body.label, ref=body.ref, source=body.source, verdict=body.verdict,
                            added_by=user["email"], at=time.time(), basis=body.basis)
    except RoomError as exc:
        raise HTTPException(422, str(exc))
    updated = store.update_room(room["id"], lambda r: (r["evidence"].append(item), r.update(updated_at=item["at"]))[0])
    store.record(user["email"], "room.evidence_added", room["id"], f"{item['kind']}: {item['label'][:80]}")
    return room_view(updated, email=user["email"], role=user["role"])


class DecisionBody(BaseModel):
    option: str
    rationale: str = Field(..., min_length=10, max_length=2000)


@app.post("/api/v1/rooms/{room_id}/decide")
def decide_room(room_id: str, body: DecisionBody, user: dict = Depends(require("rooms.decide"))) -> dict:
    room = _room(room_id, user)
    if room.get("second_approver") and room["second_approver"] != user["email"] and len(room["decisions"]) >= 1:
        raise HTTPException(409, f"this room waits for its second approver ({room['second_approver']})")
    error: list[str] = []

    def change(r: dict) -> None:
        try:
            decide(r, by=user["email"], option_id_=body.option, rationale=body.rationale, at=time.time())
        except RoomError as exc:
            error.append(str(exc))

    updated = store.update_room(room["id"], change)
    if error:
        raise HTTPException(409, error[0])
    label = next((o["label"] for o in updated["options"] if o["id"] == body.option), body.option)
    store.record(user["email"], "room.decided", updated["id"], f"{label} — {body.rationale[:120]}")
    if updated["status"] == "decided":
        store.record("fleetcontrol", "room.closed", updated["id"], f"{len(updated['decisions'])} decision(s)")
    elif updated.get("second_approver") and all(d["by"] != updated["second_approver"] for d in updated["decisions"]):
        _notify("approval.second_needed", key=f"room:{updated['id']}:second",
                ctx={"title": updated["question"], "case": updated.get("case"), "link": f"/rooms/{updated['id']}",
                     "detail": f"Waiting for {updated['second_approver']}."})
        _notify_people("room.second_approval", key=f"room:{updated['id']}:second",
                       ctx={"title": updated["question"], "case": updated.get("case"), "link": f"/rooms/{updated['id']}",
                            "detail": f"{user['email']} has decided; the room waits for you."},
                       people=[u for u in _people_with("rooms.decide", zone=updated["zone"]) if u["email"] == updated["second_approver"]])
    return room_view(updated, email=user["email"], role=user["role"])


@app.post("/api/v1/rooms/{room_id}/cancel")
def cancel_room(room_id: str, user: dict = Depends(require("rooms.open"))) -> dict:
    room = _room(room_id, user)
    if room["status"] != "open":
        raise HTTPException(409, f"this room is {room['status']}")
    if room["opened_by"] != user["email"] and user["role"] != "admin":
        raise HTTPException(403, "only the person who opened this room, or an Admin, can cancel it")
    updated = store.update_room(room["id"], lambda r: r.update(status="cancelled", closed_at=time.time(), updated_at=time.time()))
    store.record(user["email"], "room.cancelled", room["id"])
    return room_view(updated, email=user["email"], role=user["role"])


class FindingBody(BaseModel):
    agent: str = Field(..., min_length=1, max_length=120)
    text: str = Field(..., min_length=1, max_length=2000)
    verdict: Optional[str] = None
    run_id: Optional[str] = None
    # what the finding rests on (rooms.FINDING_BASES); an analytical one names the tool that computed it, and
    # session_id is the Hermes session it came from, whose recorded tool calls Fleet Control checks
    basis: Optional[str] = None
    tool: Optional[str] = Field(None, max_length=200)
    session_id: Optional[str] = Field(None, max_length=200)


# The two agent routes for rooms live with the rest of the /agent/v1 family, below `agent_instance`.


# ----------------------------------------------------------------------------- Ask the fleet (Slice 4)


def _blueprint_versions() -> tuple[list[dict], list[dict]]:
    """(the newest applied version of each blueprint, the newer unapplied drafts), parsed."""
    applied, drafts = [], []
    for versions in store.all_blueprints().values():
        done = [v for v, rec in versions.items() if rec.get("status") == "applied"]
        newest_applied = max(done) if done else None
        if newest_applied is not None:
            applied.append(versions[newest_applied]["parsed"])
        newest = max(versions)
        if newest_applied is None or newest > newest_applied:
            drafts.append(versions[newest]["parsed"])
    return applied, drafts


def _ask_config() -> Optional[dict]:
    cfg = store.get_settings().get("ask")
    return cfg if isinstance(cfg, dict) and cfg.get("instance_id") and cfg.get("profile") else None


def _ask_out(user: dict) -> dict:
    """The orchestrator, the zones it is granted, and — for an Admin, who chooses it — every profile that could be it."""
    cfg = _ask_config()
    if cfg:
        state = (store.live_state_for(cfg["instance_id"]) or {}).get(cfg["profile"]) or {}
        inst = store.get_instance(cfg["instance_id"])
        granted = granted_zones(cfg["profile"], _blueprint_versions()[0])
        mine = _my_zones(user)
        cfg = {**cfg, "model": state.get("model"), "instance_status": inst["status"] if inst else "missing",
               "granted_zones": granted, "searched_zones": [z for z in mine if z in granted],
               "not_searched": [z for z in mine if z not in granted]}
    candidates = []
    if user["role"] == "admin":
        for inst in store.list_instances():
            live = store.live_state_for(inst["id"]) or {}
            if inst["mode"] == "agent" and inst.get("agent_version") and live:
                candidates.append({"instance_id": inst["id"], "environment": inst["environment"], "profiles": sorted(live)})
    return {"config": cfg, "candidates": candidates}


class AskConfig(BaseModel):
    instance_id: str
    profile: str


@app.get("/api/v1/ask/config")
def get_ask_config(user: dict = Depends(require("ask.use"))) -> dict:
    return _ask_out(user)


@app.put("/api/v1/ask/config")
def set_ask_config(body: AskConfig, user: dict = Depends(require("settings.manage"))) -> dict:
    inst = _require_instance(body.instance_id)
    if inst["mode"] != "agent" or not inst.get("agent_version"):
        raise HTTPException(409, f"{body.instance_id} has no paired Fleet Control Agent; questions go through it")
    if body.profile not in (store.live_state_for(body.instance_id) or {}):
        raise HTTPException(422, f"no profile {body.profile!r} in the last import from {body.instance_id}; import live profiles first")
    store.set_settings({"ask": {"instance_id": body.instance_id, "profile": body.profile}}, user["email"])
    store.record(user["email"], "ask.configured", f"{body.profile} on {body.instance_id}")
    return _ask_out(user)


class AskAsset(BaseModel):
    instance_id: str
    zones: list[str] = Field(..., min_length=1, max_length=20)


@app.post("/api/v1/ask/blueprint-asset", status_code=201)
def create_ask_asset(body: AskAsset, user: dict = Depends(require("settings.manage"))) -> dict:
    """The fleet-control-orchestrator blueprint (one tool-less fc-orchestrator profile granted these zones) for an
    instance, to plan and apply like any blueprint; then choose fc-orchestrator as the orchestrator."""
    inst = _require_instance(body.instance_id)
    unknown = sorted(z for z in set(body.zones) if not store.get_zone(z))
    if unknown:
        raise HTTPException(422, f"no such content zone: {', '.join(unknown)}")
    live = store.live_state_for(body.instance_id) or {}
    source = live.get("default") or next(iter(live.values()), None)
    model = (source or {}).get("model") or {}
    if not (model.get("provider") and model.get("name")):
        raise HTTPException(409, f"import live profiles from {body.instance_id} first: the orchestrator uses its default model")
    version = store.next_blueprint_version(ORCHESTRATOR_BLUEPRINT)
    bp = orchestrator_blueprint(model, zones=body.zones, instance_id=body.instance_id, environment=inst["environment"],
                                owner=user["email"], version=version)
    rec = store.save_blueprint(ORCHESTRATOR_BLUEPRINT, version, dump_blueprint(bp), bp.model_dump(mode="json"), user["email"])
    store.record(user["email"], "blueprint.saved", f"{ORCHESTRATOR_BLUEPRINT} v{version}",
                 f"Ask the fleet orchestrator for {body.instance_id}: {', '.join(sorted(set(body.zones)))}")
    return {"name": rec["name"], "version": rec["version"], "status": rec["status"]}


def _ask_candidates(zones: list[str]) -> list[dict]:
    """Everything in these zones an answer could rest on: files and outputs with text, and Decision Rooms."""
    out = []
    for f in store.list_files(zones):
        if f.get("text"):
            out.append({"key": f"file:{f['id']}", "kind": "file", "ref": f["id"], "label": f["name"], "zone": f["zone"],
                        "classification": f.get("classification"), "text": f["text"]})
    for o in store.list_outputs(zones):
        if o.get("text"):
            out.append({"key": f"output:{o['id']}", "kind": "output", "ref": o["id"], "zone": o["zone"],
                        "label": o["name"] + (f" · {o['case']}" if o.get("case") else ""),
                        "classification": o.get("classification"), "text": o["text"]})
    for r in store.list_rooms(zones):
        out.append({"key": f"room:{r['id']}", "kind": "room", "ref": r["id"], "zone": r["zone"], "classification": None,
                    "label": "Decision Room: " + r["question"] + (f" · {r['case']}" if r.get("case") else ""), "text": room_text(r)})
    return out


def _ask_thread(thread_id: str, user: dict) -> dict:
    t = store.get_ask_thread(thread_id)
    if not t or t["owner"] != user["email"]:  # a conversation is its owner's alone
        raise HTTPException(404, "no such conversation")
    return t


def _ask_turn(thread: dict, question: str, user: dict) -> dict:
    """Choose the sources, file the turn, and queue the orchestrator's run."""
    cfg = _ask_config()
    if not cfg:
        raise HTTPException(409, "no orchestrator is chosen yet: an Admin chooses it on Ask the fleet")
    inst = store.get_instance(cfg["instance_id"])
    if not inst or inst["mode"] != "agent" or not inst.get("agent_version"):
        raise HTTPException(409, f"the orchestrator's instance {cfg['instance_id']} has no paired Fleet Control Agent")
    granted = granted_zones(cfg["profile"], _blueprint_versions()[0])
    mine = _my_zones(user)
    searched = [z for z in mine if z in granted]
    carry = [k for t in thread["turns"] if t["status"] == "answered" for k in t.get("cited_keys") or []]
    sources = select_sources(question, _ask_candidates(searched), carry=carry)
    turn_no = len(thread["turns"]) + 1
    turn = {"n": turn_no, "question": question, "asked_at": time.time(), "status": "running", "job_id": None,
            "orchestrator": {"instance_id": cfg["instance_id"], "profile": cfg["profile"]},
            "searched_zones": searched, "not_searched": [z for z in mine if z not in granted],
            "sources": [{k: v for k, v in s.items() if k != "excerpt"} | {"chars": len(s["excerpt"])} for s in sources],
            "answer": None, "cited": [], "cited_keys": [], "dropped": [], "format": None, "grounding": None,
            "tool_calls": None, "error": None, "run": None, "answered_at": None, "saved_output": None}
    store.update_ask_thread(thread["id"], lambda d: d["turns"].append(turn))
    params = {"profile": cfg["profile"], "input": ask_request_text(question, thread["turns"]),
              "instructions": ask_instructions(asker=user.get("name") or user["email"], role=role_label(user["role"]), sources=sources),
              "timeout": 300, "transcript": True}
    job = store.enqueue_job(cfg["instance_id"], "hermes_run", params, {"ask_thread": thread["id"], "turn": turn_no})

    def set_job(d: dict) -> None:
        for t in d["turns"]:
            if t["n"] == turn_no:
                t["job_id"] = job["id"]

    store.record(user["email"], "ask.asked", thread["id"], f"#{turn_no}: {question[:160]} ({len(sources)} sources)")
    return store.update_ask_thread(thread["id"], set_job)


def _ask_result(job: dict) -> None:
    """The orchestrator answered (or did not): check the citations and the transcript, and file the answer."""
    thread_id, turn_no = job["meta"]["ask_thread"], job["meta"]["turn"]
    result = job.get("result") or {}
    answer, error = None, None
    thread = store.get_ask_thread(thread_id)
    turn = next((t for t in (thread or {}).get("turns", []) if t["n"] == turn_no), None)
    if not turn or turn["status"] != "running":
        return  # stopped meanwhile: a late answer does not revive it
    if job["status"] == "done":
        try:
            answer = parse_answer(result.get("output") or "", [s["id"] for s in turn["sources"]])
        except AskError as exc:
            error = str(exc)
    else:
        error = result.get("error") or f"the run ended {result.get('status') or 'without a result'}"
    tool_calls = result.get("tool_calls") if "tool_calls" in result else None

    def file(d: dict) -> None:
        for t in d["turns"]:
            if t["n"] != turn_no or t["status"] != "running":
                continue
            t.update(status="answered" if answer else "failed", error=error, answered_at=time.time(), tool_calls=tool_calls,
                     run={"run_id": result.get("run_id"), "usage": result.get("usage"), "session_id": result.get("session_id")})
            if answer:
                by_id = {s["id"]: s for s in t["sources"]}
                t.update(answer=answer["answer"], cited=answer["cited"], dropped=answer["dropped"], format=answer["format"],
                         cited_keys=[by_id[i]["key"] for i in answer["cited"]],
                         grounding=grounding(answer["cited"], tool_calls, evidence_error=result.get("evidence_error")))

    store.update_ask_thread(thread_id, file)
    if answer and thread:
        owner = store.get_user(thread["owner"])
        if owner:
            _notify_people("ask.answered", key=f"ask:{thread_id}:{turn_no}", people=[owner],
                           ctx={"link": f"/ask?c={thread_id}", "detail": f"Verdict: {grounding(answer['cited'], tool_calls)['verdict']}."})
    detail = f"#{turn_no}: cites {', '.join(answer['cited']) or 'nothing'}" if answer else f"#{turn_no}: {(error or '')[:160]}"
    store.record("fleetcontrol", "ask.answered" if answer else "ask.failed", thread_id, detail)


class AskQuestion(BaseModel):
    question: str = Field(..., min_length=3, max_length=1000)


@app.get("/api/v1/ask/threads")
def list_ask_threads(user: dict = Depends(require("ask.use"))) -> list[dict]:
    return [thread_row(t) for t in store.list_ask_threads(user["email"])]


@app.post("/api/v1/ask/threads", status_code=201)
def start_ask_thread(body: AskQuestion, user: dict = Depends(require("ask.use"))) -> dict:
    try:
        question = check_question(body.question)
    except AskError as exc:
        raise HTTPException(422, str(exc))
    if not _ask_config():
        raise HTTPException(409, "no orchestrator is chosen yet: an Admin chooses it on Ask the fleet")
    now = time.time()
    thread = store.save_ask_thread({"id": "ask_" + uuid.uuid4().hex[:10], "owner": user["email"], "created_at": now,
                                    "updated_at": now, "turns": []})
    return _ask_turn(thread, question, user)


@app.get("/api/v1/ask/threads/{thread_id}")
def get_ask_thread(thread_id: str, user: dict = Depends(require("ask.use"))) -> dict:
    return _ask_thread(thread_id, user)


@app.post("/api/v1/ask/threads/{thread_id}/turns", status_code=201)
def ask_follow_up(thread_id: str, body: AskQuestion, user: dict = Depends(require("ask.use"))) -> dict:
    thread = _ask_thread(thread_id, user)
    if any(t["status"] == "running" for t in thread["turns"]):
        raise HTTPException(409, "the last question is still being answered")
    try:
        question = check_question(body.question)
    except AskError as exc:
        raise HTTPException(422, str(exc))
    return _ask_turn(thread, question, user)


@app.post("/api/v1/ask/threads/{thread_id}/turns/{turn_no}/stop")
def stop_ask_turn(thread_id: str, turn_no: int, user: dict = Depends(require("ask.use"))) -> dict:
    """Stop waiting for an answer (the instance may be gone); a late answer is then ignored."""
    thread = _ask_thread(thread_id, user)
    turn = next((t for t in thread["turns"] if t["n"] == turn_no), None)
    if not turn:
        raise HTTPException(404, "no such question in this conversation")
    if turn["status"] != "running":
        raise HTTPException(409, f"this question is already {turn['status']}")
    job = store.get_job(turn["job_id"]) if turn.get("job_id") else None
    if job and job["status"] in ("queued", "running"):
        store.complete_job(job["id"], {"ok": False, "error": f"stopped by {user['email']}"}, instance_id=job["instance_id"])

    def stop(d: dict) -> None:
        for t in d["turns"]:
            if t["n"] == turn_no and t["status"] == "running":
                t.update(status="failed", error=f"stopped by {user['email']}", answered_at=time.time())

    store.record(user["email"], "ask.stopped", thread_id, f"#{turn_no}")
    return store.update_ask_thread(thread_id, stop)


class AskSave(BaseModel):
    zone: str
    name: str = Field(..., min_length=1, max_length=200)
    case: Optional[str] = Field(None, max_length=64)


@app.get("/api/v1/ask/threads/{thread_id}/turns/{turn_no}/save-targets")
def ask_save_targets(thread_id: str, turn_no: int, user: dict = Depends(require("ask.use"))) -> dict:
    thread = _ask_thread(thread_id, user)
    turn = next((t for t in thread["turns"] if t["n"] == turn_no), None)
    if not turn or turn["status"] != "answered":
        raise HTTPException(404, "no answer to save")
    cited = [s for s in turn["sources"] if s["id"] in turn["cited"]]
    return {"zones": save_targets(store.list_zones(), mine=_my_zones(user), cited_zones=[s["zone"] for s in cited]),
            "classification": classification_of(s.get("classification") for s in cited)}


@app.post("/api/v1/ask/threads/{thread_id}/turns/{turn_no}/save", status_code=201)
def save_ask_answer(thread_id: str, turn_no: int, body: AskSave, user: dict = Depends(require("ask.use"))) -> dict:
    """Keep an answer as a fleet output, in a zone no wider than the sources it cites, with their classification."""
    thread = _ask_thread(thread_id, user)
    turn = next((t for t in thread["turns"] if t["n"] == turn_no), None)
    if not turn or turn["status"] != "answered":
        raise HTTPException(404, "no answer to save")
    cited = [s for s in turn["sources"] if s["id"] in turn["cited"]]
    allowed = save_targets(store.list_zones(), mine=_my_zones(user), cited_zones=[s["zone"] for s in cited])
    if body.zone not in allowed:
        raise HTTPException(422, "that zone would show this answer to people who cannot read all of its sources; "
                                 f"choose one of: {', '.join(allowed) or 'none'}")
    orch = turn["orchestrator"]
    lines = [turn["answer"], "", f"Question: {turn['question']}", "Sources:"]
    lines += [f"[{s['id']}] {s['label']} ({s['kind']} {s['ref']}, zone {s['zone']})" for s in cited]
    lines.append(f"Grounding: {turn['grounding']['verdict']} — {turn['grounding']['detail']}")
    try:
        out = new_output(output_id="out_" + uuid.uuid4().hex[:10], zone=body.zone, name=body.name, kind="summary",
                         classification=classification_of(s.get("classification") for s in cited),
                         produced_by=orch["profile"], at=time.time(), text="\n".join(lines), case=body.case,
                         instance_id=orch["instance_id"],
                         source={"kind": "ask", "thread": thread_id, "turn": turn_no, "asked_by": user["email"],
                                 "run_id": (turn.get("run") or {}).get("run_id"), "cites": [s["key"] for s in cited]})
    except OutputError as exc:
        raise HTTPException(422, str(exc))
    store.save_output(out)
    _output_event(out)

    def mark(d: dict) -> None:
        for t in d["turns"]:
            if t["n"] == turn_no:
                t["saved_output"] = out["id"]

    store.update_ask_thread(thread_id, mark)
    store.record(user["email"], "output.saved_from_ask", out["id"], f"{thread_id} #{turn_no} → {body.zone}")
    return output_row(out)


# ----------------------------------------------------------------------------- Messaging (Slice 4)


def _messaging_on() -> None:
    """Messaging can be switched off for the whole workspace (Settings → General); then its API says so."""
    if not _settings()["messaging_enabled"]:
        raise HTTPException(409, "Messaging is off (Settings → General)")


def _channel_out(ch: dict, states: dict) -> dict:
    return {**ch, **channel_health(ch, states.get(ch["instance_id"]))}


def _notify(event: str, *, key: str, ctx: dict) -> None:
    """A fleet event happened: send it to every channel an applied blueprint's delivery rule names for it, once."""
    if not _settings()["messaging_enabled"]:
        return  # switched off: nothing is sent, and nothing is queued to be sent later
    applied, _ = _blueprint_versions()
    channels = {c["ref"]: c for c in store.list_channels() if not c.get("direct")}
    portal = _settings()["portal_url"]
    for rule in rules_from(applied, channels.values()):
        if rule["when"] != event or not rule["enabled"]:
            continue
        ch = channels.get(rule["to"])
        if not ch or not ch.get("enabled", True):
            continue  # the Messaging screen shows a rule whose channel is missing; nothing to send to
        text = render(event, rule["template"], ctx, show_titles=ch["show_titles"], portal_url=portal)
        _deliver(ch, event=event, key=key, text=text, rule=rule)


def _deliver(ch: dict, *, event: str, key: str, text: str, rule: Optional[dict] = None, by: Optional[str] = None,
             chat_id: Optional[str] = None, to: Optional[str] = None) -> Optional[dict]:
    if ch.get("direct") and not (chat_id or "").strip():
        # Hermes sends an empty chat_id to the platform's home channel: a personal message must never go there
        raise ValueError(f"a direct message on {ch['id']} needs its recipient's address")
    doc = store.add_delivery(new_delivery(delivery_id="dlv_" + uuid.uuid4().hex[:10], channel=ch, event=event, key=key,
                                          text=text, at=time.time(), rule=rule, by=by, to=to))
    if not doc:
        return None  # this event already went to this channel (or this person)
    params = {"route": ch["route"], "text": text, "delivery_id": doc["id"]}
    if ch.get("direct"):
        params.update(chat_id=chat_id, direct=True)
    job = store.enqueue_job(ch["instance_id"], "deliver_message", params, {"delivery": doc["id"]})
    return store.update_delivery(doc["id"], job_id=job["id"], status="sent")


def _delivery_result(job: dict) -> None:
    result = job.get("result") or {}
    ok = job["status"] == "done" and result.get("ok", True)
    store.update_delivery(job["meta"]["delivery"], status="delivered" if ok else "failed", finished_at=time.time(),
                          error=None if ok else (result.get("error") or "the agent could not deliver it"))


def _messaging_result(job: dict, instance_id: str) -> None:
    if job["kind"] == "messaging_discover" and job["status"] == "done":
        result = job.get("result") or {}
        store.save_messaging_state(instance_id, {"platforms": merge_gateway(result.get("platforms") or [], result.get("gateway")),
                                                 "webhooks": result.get("webhooks") or {}, "gateway": result.get("gateway")},
                                   time.time())
    channel_id = job["meta"].get("channel")
    if job["kind"] == "channel_route" and channel_id:
        ch = store.get_channel(channel_id)
        if ch:
            result = job.get("result") or {}
            store.save_channel({**ch, "route_job": {"id": job["id"], "status": job["status"], "error": result.get("error"),
                                                    "at": time.time()}})
    if job["kind"] in ("channel_route", "webhooks_enable"):  # see what changed
        store.enqueue_job(instance_id, "messaging_discover", {}, {"messaging": instance_id})


def _agent_instance_or_409(instance_id: str) -> dict:
    inst = _require_instance(instance_id)
    if inst["mode"] != "agent" or not inst.get("agent_version"):
        raise HTTPException(409, f"{instance_id} has no paired Fleet Control Agent; messaging goes through it")
    return inst


@app.get("/api/v1/messaging")
def get_messaging(user: dict = Depends(require("messaging.read"))) -> dict:
    """Channels with their health, the delivery rules of the applied blueprints, the platforms each instance has, and
    the latest deliveries."""
    _messaging_on()
    states = store.messaging_states()
    channels = [_channel_out(c, states) for c in store.list_channels()]
    applied, drafts = _blueprint_versions()
    instances = []
    for i in store.list_instances():
        st = states.get(i["id"])
        instances.append({"instance_id": i["id"], "environment": i["environment"],
                          "can_discover": i["mode"] == "agent" and bool(i.get("agent_version")),
                          "discovered_at": st["at"] if st else None,
                          "webhooks_enabled": bool((st or {}).get("webhooks", {}).get("enabled")) if st else None,
                          "gateway": (st or {}).get("gateway"),
                          "platforms": [{k: p.get(k) for k in ("id", "name", "enabled", "configured", "gateway_running", "state",
                                                               "dashboard_state", "error_message", "home_channel")}
                                        for p in (st or {}).get("platforms") or []]})
    return {"channels": channels, "rules": rules_from(applied, channels), "events": EVENTS, "templates": list(TEMPLATES),
            "drafts": [{"name": d["metadata"]["name"], "version": d["metadata"]["version"]} for d in drafts],
            "instances": instances, "deliveries": store.list_deliveries(limit=30), "portal_url": _settings()["portal_url"]}


class MessagingInstance(BaseModel):
    instance_id: str


@app.post("/api/v1/messaging/discover")
def discover_messaging(body: MessagingInstance, user: dict = Depends(require("messaging.read"))) -> dict:
    _messaging_on()
    _agent_instance_or_409(body.instance_id)
    job = store.enqueue_job(body.instance_id, "messaging_discover", {}, {"messaging": body.instance_id})
    return {"job_id": job["id"]}


@app.post("/api/v1/messaging/enable-webhooks")
def enable_webhooks(body: MessagingInstance, user: dict = Depends(require("messaging.manage"))) -> dict:
    """Turn on the webhook platform on an instance. Hermes restarts its gateway to start it."""
    _messaging_on()
    _agent_instance_or_409(body.instance_id)
    job = store.enqueue_job(body.instance_id, "webhooks_enable", {}, {"messaging": body.instance_id})
    store.record(user["email"], "messaging.webhooks_enabled", body.instance_id, "the gateway restarts")
    return {"job_id": job["id"]}


class ChannelCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=60)
    instance_id: str
    platform: str = Field(..., min_length=2, max_length=32)
    chat_id: Optional[str] = Field(None, max_length=120)
    audience: str = Field("", max_length=120)
    show_titles: bool = False
    direct: bool = False  # one message per person, at the address they give in Settings → Notifications


def _route_job(ch: dict, action: str) -> dict:
    return store.enqueue_job(ch["instance_id"], "channel_route",
                             {"action": action, "route": ch["route"], "platform": ch["platform"], "chat_id": ch.get("chat_id"),
                              "channel": ch["ref"]}, {"channel": ch["id"]})


@app.post("/api/v1/messaging/channels", status_code=201)
def create_channel(body: ChannelCreate, user: dict = Depends(require("messaging.manage"))) -> dict:
    _messaging_on()
    _agent_instance_or_409(body.instance_id)
    try:
        ch = new_channel(name=body.name, platform=body.platform, instance_id=body.instance_id, by=user["email"], at=time.time(),
                         chat_id=body.chat_id, audience=body.audience, show_titles=body.show_titles, direct=body.direct)
    except MessagingError as exc:
        raise HTTPException(422, str(exc))
    if store.get_channel(ch["id"]):
        raise HTTPException(409, f"there is already a channel {ch['id']}")
    store.save_channel(ch)
    job = _route_job(ch, "create")
    store.record(user["email"], "messaging.channel_created", ch["ref"], f"on {ch['instance_id']}, route {ch['route']}")
    return _channel_out(store.save_channel({**ch, "route_job": {"id": job["id"], "status": "queued", "error": None, "at": time.time()}}),
                        store.messaging_states())


class ChannelChange(BaseModel):
    audience: Optional[str] = Field(None, max_length=120)
    show_titles: Optional[bool] = None
    enabled: Optional[bool] = None


def _channel(channel_id: str) -> dict:
    ch = store.get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "no such channel")
    return ch


@app.patch("/api/v1/messaging/channels/{channel_id}")
def change_channel(channel_id: str, body: ChannelChange, user: dict = Depends(require("messaging.manage"))) -> dict:
    _messaging_on()
    ch = _channel(channel_id)
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    ch = store.save_channel({**ch, **changes})
    store.record(user["email"], "messaging.channel_changed", ch["ref"], ", ".join(f"{k}={v}" for k, v in changes.items()))
    return _channel_out(ch, store.messaging_states())


@app.post("/api/v1/messaging/channels/{channel_id}/route")
def recreate_route(channel_id: str, user: dict = Depends(require("messaging.manage"))) -> dict:
    _messaging_on()
    ch = _channel(channel_id)
    _agent_instance_or_409(ch["instance_id"])
    job = _route_job(ch, "create")
    store.record(user["email"], "messaging.route_recreated", ch["ref"], ch["route"])
    return _channel_out(store.save_channel({**ch, "route_job": {"id": job["id"], "status": "queued", "error": None, "at": time.time()}}),
                        store.messaging_states())


@app.delete("/api/v1/messaging/channels/{channel_id}")
def delete_channel(channel_id: str, user: dict = Depends(require("messaging.manage"))) -> dict:
    """Forget a channel and ask its instance to drop the route. Deliveries stay: they are the record of what was sent."""
    _messaging_on()
    ch = _channel(channel_id)
    inst = store.get_instance(ch["instance_id"])
    if inst and inst["mode"] == "agent" and inst.get("agent_version"):
        _route_job(ch, "remove")
    store.delete_channel(channel_id)
    store.record(user["email"], "messaging.channel_removed", ch["ref"], ch["route"])
    return {"ok": True}


@app.post("/api/v1/messaging/channels/{channel_id}/test", status_code=201)
def test_channel(channel_id: str, user: dict = Depends(require("messaging.manage"))) -> dict:
    _messaging_on()
    ch = _channel(channel_id)
    _agent_instance_or_409(ch["instance_id"])
    chat_id = None
    if ch.get("direct"):
        prefs = notify_effective(store.get_notify_prefs(user["email"]), user["role"])
        chat_id = prefs["addresses"].get(ch["platform"])
        if not chat_id:
            raise HTTPException(409, f"a direct-message channel tests by writing to you: give your {ch['platform']} address "
                                     "in Settings → Notifications first")
    doc = _deliver(ch, event="test", key=f"test:{uuid.uuid4().hex}", text=test_text(ch, user["email"], _settings()["portal_url"]),
                   by=user["email"], chat_id=chat_id, to=user["email"] if chat_id else None)
    store.record(user["email"], "messaging.test_sent", ch["ref"], doc["id"])
    return doc


@app.get("/api/v1/messaging/deliveries")
def list_deliveries(channel: Optional[str] = None, user: dict = Depends(require("messaging.read"))) -> list[dict]:
    _messaging_on()
    return store.list_deliveries(channel=channel, limit=100)


class DeliveryRules(BaseModel):
    rules: list[dict[str, Any]] = Field(..., max_length=50)


@app.put("/api/v1/blueprints/{name}/{version}/delivery")
def save_delivery_rules(name: str, version: int, body: DeliveryRules, user: dict = Depends(require("blueprints.write"))) -> dict:
    """A draft's delivery rules, all at once: rules are part of the blueprint, so they reach the fleet by plan and apply."""
    rec = _draft(name, version)
    parsed = {**rec["parsed"], "delivery": body.rules}
    try:
        new = Blueprint.model_validate(parsed)
    except ValidationError as exc:
        raise HTTPException(422, "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]))
    store.update_blueprint(name, version, dump_blueprint(new), new.model_dump(mode="json"), user["email"])
    store.record(user["email"], "blueprint.delivery_saved", f"{name} v{version}", f"{len(new.delivery)} rules")
    return {"name": name, "version": version, "delivery": [r.model_dump(mode="json") for r in new.delivery]}


# ----------------------------------------------------------------------------- Access inspector (Slice 4)


def _mcp_servers(user: dict) -> dict[str, dict]:
    rows = list_integrations(user)["integrations"]
    return {r["name"]: {"auth": r.get("auth"), "health": r.get("health"), "tools": [t["name"] for t in r.get("tools") or []]}
            for r in rows if r["kind"] == "mcp"}


@app.get("/api/v1/access/subjects")
def access_subjects(user: dict = Depends(require("users.read"))) -> dict:
    """What the inspector can be asked about: people, the agents of applied blueprints, tools, open rooms."""
    applied, _ = _blueprint_versions()
    agents = [{"blueprint": p["metadata"]["name"], "version": p["metadata"].get("version"), "agent": a["id"],
               "profile": a.get("hermes_profile") or a["id"]} for p in applied for a in p.get("agents") or []]
    servers = _mcp_servers(user)
    tools: set[str] = set(servers)
    for name, s in servers.items():
        tools |= {f"{name}.{t}" for t in s["tools"]}
    for p in applied:
        for pol in p.get("policies") or []:
            tools |= set((pol.get("params") or {}).get("tools") or [])
        for a in p.get("agents") or []:
            tools |= set(a.get("toolsets") or [])
    return {"people": [{"email": u["email"], "name": u["name"], "role": u["role"], "role_label": role_label(u["role"]),
                        "disabled": u.get("disabled", False)} for u in store.list_users()],
            "agents": agents, "tools": sorted(tools),
            "rooms": [{"id": r["id"], "question": r["question"], "zone": r["zone"], "case": r.get("case")}
                      for r in store.list_rooms(None, status="open")]}


@app.get("/api/v1/access/inspect")
def access_inspect(email: Optional[str] = None, blueprint: Optional[str] = None, agent: Optional[str] = None,
                   tool: Optional[str] = None, room: Optional[str] = None, environment: Optional[str] = None,
                   user: dict = Depends(require("users.read"))) -> dict:
    """Effective access: the person, the agent, and what both reach together; with a tool, room or environment, the
    one answer to "may they?" and the rule behind it. Reads only."""
    zones = store.list_zones()
    out: dict[str, Any] = {"person": None, "agent": None, "together": None, "checks": []}
    person = None
    if email:
        person = store.get_user(email.strip().lower())
        if not person:
            raise HTTPException(404, f"no account {email}")
        perms, pz = person_permissions(person["role"]), person_zones(person["role"], zones)
        out["person"] = {"email": person["email"], "name": person["name"], "role": person["role"],
                         "role_label": role_label(person["role"]), "disabled": person.get("disabled", False),
                         "permissions": perms, "zones": pz, "summary": access_summary(perms, pz)}
        if person.get("disabled"):
            out["checks"].append({"question": f"May {person['email']} sign in?", "allowed": False, "why": "the account is disabled"})
    grants = None
    if agent:
        applied, _ = _blueprint_versions()
        parsed = next((p for p in applied if p["metadata"]["name"] == blueprint), None) if blueprint else \
            next((p for p in applied if any(a["id"] == agent for a in p.get("agents") or [])), None)
        grants = agent_grants(parsed, agent) if parsed else None
        if not grants:
            raise HTTPException(404, f"no agent {agent} in an applied blueprint" + (f" {blueprint}" if blueprint else ""))
        out["agent"] = {**grants, "zones": [{"zone": z["id"], "name": z.get("name") or z["id"], "allowed": z["id"] in grants["content_zones"],
                                              "why": "granted by content_zones" if z["id"] in grants["content_zones"] else "not in its content_zones"}
                                             for z in zones]}
    if out["person"] and grants:
        out["together"] = {"zones": combined_zones(out["person"]["zones"], grants),
                           "note": "Through this agent (Ask the fleet, rooms it files into), a person sees only what both may read."}
    if tool:
        if not grants:
            raise HTTPException(422, "choose an agent to check a tool: tools are called by agents, never by people directly")
        v = tool_verdict(grants, tool, servers=_mcp_servers(user))
        out["checks"].append({"question": f"May {grants['agent']} call {tool}?", **v})
    if room:
        if not person:
            raise HTTPException(422, "choose a person to check a room")
        r = store.get_room(room)
        if not r:
            raise HTTPException(404, "no such room")
        out["checks"].append({"question": f"May {person['email']} decide in “{r['question'][:80]}”?",
                              **why_room(r, email=person["email"], role=person["role"], zones=zones)})
    if environment:
        if not person:
            raise HTTPException(422, "choose a person to check applying a plan")
        if environment not in ("lab", "staging", "production"):
            raise HTTPException(422, "environment is lab, staging or production")
        out["checks"].append({"question": f"May {person['email']} apply a plan to {environment}?", **why_apply(environment, person["role"])})
    return out


# ----------------------------------------------------------------------------- Notifications (Slice 4)

# what a personal message is called, and whether it asks for a decision (then it says a reply is not one)
_PERSONAL_HEAD = {"room.waiting": ("Decision waiting for you", "decision-request"),
                  "room.second_approval": ("Your second approval is needed", "approval-request"),
                  "plan.approval": ("A production plan needs your approval", "alert"),
                  "ask.answered": ("The fleet answered your question", "alert"),
                  "apply.failed": ("Apply failed", "alert"), "drift.detected": ("Drift detected", "alert"),
                  "assurance.no_evidence": ("Assurance: No evidence", "alert"),
                  "gate.waiting": ("A workflow waits for your approval", "decision-request"),
                  "gate.escalated": ("An overdue workflow approval was escalated to you", "decision-request")}


def _direct_channels() -> dict[str, dict]:
    """{platform: channel}: the enabled direct-message channel of each platform (the first, if there are several)."""
    out: dict[str, dict] = {}
    for ch in store.list_channels():
        if ch.get("direct") and ch.get("enabled", True):
            out.setdefault(ch["platform"], ch)
    return out


def _notify_people(event: str, *, key: str, ctx: dict, people: Iterable[dict]) -> None:
    """Tell each of these people, if they asked for this event and can be reached. Callers pass only people who may
    see what the event is about."""
    if not _settings()["messaging_enabled"]:
        return
    direct = _direct_channels()
    if not direct:
        return
    head, template = _PERSONAL_HEAD[event]
    portal = _settings()["portal_url"]
    for person in people:
        if person.get("disabled"):
            continue
        prefs = notify_effective(store.get_notify_prefs(person["email"]), person["role"])
        reach = notify_reach(prefs, event)
        ch = direct.get(reach[0]) if reach else None
        if not ch:
            continue
        text = render(event, template, ctx, show_titles=prefs["show_titles"], portal_url=portal, head=head)
        _deliver(ch, event=event, key=f"{key}:to:{person['email']}", text=text, chat_id=reach[1], to=person["email"])


def _people_with(permission: str, *, zone: Optional[str] = None, except_: Iterable[str] = ()) -> list[dict]:
    """Active accounts whose role has ``permission`` (and reads ``zone``, when given)."""
    z = store.get_zone(zone) if zone else None
    skip = set(except_)
    return [u for u in store.list_users()
            if allowed(u["role"], permission) and u["email"] not in skip and not u.get("disabled")
            and (z is None or may_read(z, u["role"]))]


def _notifications_out(user: dict) -> dict:
    prefs = notify_effective(store.get_notify_prefs(user["email"]), user["role"])
    states = store.messaging_states()
    platforms = [{"platform": p, "channel": ch["id"], "instance_id": ch["instance_id"], **channel_health(ch, states.get(ch["instance_id"]))}
                 for p, ch in sorted(_direct_channels().items())]
    return {"prefs": prefs, "platforms": platforms, "messaging_enabled": bool(_settings()["messaging_enabled"]),
            "events": [{"event": e, "label": PERSONAL_EVENTS[e][0], "on": prefs["events"][e]} for e in prefs["events"]]}


@app.get("/api/v1/me/notifications")
def get_my_notifications(user: dict = Depends(current_user)) -> dict:
    """Your own notification choices. Your address is yours: nobody else reads or sets it."""
    return _notifications_out(user)


class NotificationChange(BaseModel):
    via: Optional[str] = Field(None, max_length=32)
    address: Optional[str] = Field(None, max_length=120)
    events: Optional[dict[str, bool]] = None
    show_titles: Optional[bool] = None
    clear: bool = False


@app.patch("/api/v1/me/notifications")
def change_my_notifications(body: NotificationChange, user: dict = Depends(current_user)) -> dict:
    if body.via and body.via not in _direct_channels():
        raise HTTPException(422, f"there is no direct-message channel for {body.via}: an Admin adds one in Messaging")
    try:
        prefs = notify_update(store.get_notify_prefs(user["email"]) or {}, user["role"], via=body.via, address=body.address,
                              events=body.events, show_titles=body.show_titles, clear=body.clear)
    except NotificationError as exc:
        raise HTTPException(422, str(exc))
    store.save_notify_prefs(user["email"], prefs)
    what = "cleared" if body.clear else ", ".join(k for k, v in body.model_dump(exclude_unset=True).items() if v is not None)
    store.record(user["email"], "notifications.changed", user["email"], what)  # never the address itself
    return _notifications_out(user)


@app.post("/api/v1/me/notifications/test", status_code=201)
def test_my_notifications(user: dict = Depends(current_user)) -> dict:
    _messaging_on()
    prefs = notify_effective(store.get_notify_prefs(user["email"]), user["role"])
    via = prefs["via"]
    address = prefs["addresses"].get(via) if via else None
    ch = _direct_channels().get(via) if via else None
    if not (via and address and ch):
        raise HTTPException(409, "choose how to be reached and give your address first")
    text = f"Fleet Control · Test notification\nFor {user['email']}.\nOpen: {_settings()['portal_url'].rstrip('/')}/settings"
    doc = _deliver(ch, event="test", key=f"test:{uuid.uuid4().hex}", text=text, chat_id=address, to=user["email"], by=user["email"])
    return {k: doc[k] for k in ("id", "status", "channel", "at")}


# ----------------------------------------------------------------------------- Workflows (Slice 5)

WF_STEP_TIMEOUT = 900  # seconds one agent step may take on the instance
WF_ROOM_OPTIONS = ["Approve the outcome", "Send it back for more work", "Reject"]


def _wf_agents(parsed: dict) -> dict[str, dict]:
    return {a["id"]: {"profile": a.get("hermes_profile") or a["id"], "mcps": list(a.get("mcps") or []),
                      "content_zones": list(a.get("content_zones") or [])} for a in parsed.get("agents") or []}


def _mcp_health() -> dict[tuple[str, str, str], tuple[str, Optional[str]]]:
    """{(instance, profile, server): (health, error)} from the last MCP discoveries (Integrations)."""
    rows = list_integrations({"email": "fleetcontrol", "role": "admin"})["integrations"]
    return {(e["instance"], e["profile"], r["name"]): (e["health"], e.get("error"))
            for r in rows if r["kind"] == "mcp" for e in r.get("profile_health") or []}


def _wf_readiness(parsed: dict, steps: list[dict]) -> list[dict]:
    """Per instance with a paired agent: can this workflow run there, and if not, why (a missing profile or MCP server).
    ``warnings`` do not block: an MCP server the last discovery found unreachable from the agent's profile (an expired
    login, typically) will fail that step, so it is said before the run starts rather than found by a worker."""
    agents = _wf_agents(parsed)
    health = _mcp_health()
    out = []
    for inst in store.list_instances():
        if inst["mode"] != "agent" or not inst.get("agent_version"):
            continue
        live = store.live_state_for(inst["id"]) or {}
        mcps = {m for state in live.values() for m in (state.get("mcps") or [])}
        problems = [f"{a} runs as profile {agents[a]['profile']}, which {inst['id']} does not have"
                    for a in agents_used(steps) if a in agents and agents[a]["profile"] not in live]
        problems += missing_requirements(steps, agents, mcps)
        warnings = []
        for a in agents_used(steps):
            profile = agents.get(a, {}).get("profile")
            for mcp in agents.get(a, {}).get("mcps", []):
                state, error = health.get((inst["id"], profile, mcp), (None, None))
                if state == "unreachable":
                    warnings.append(f"{mcp} was unreachable from {profile} at the last discovery"
                                    + (f" ({error[:160]})" if error else "") + ": log the profile in again, then Discover")
        out.append({"instance_id": inst["id"], "environment": inst["environment"], "ready": not problems, "problems": problems,
                    "warnings": warnings, "kanban": _wf_kanban(inst)})
    return out


@app.get("/api/v1/workflows")
def list_workflows(user: dict = Depends(require("blueprints.read"))) -> list[dict]:
    """Every workflow of the applied blueprints: its steps as a run executes them, and where it can run."""
    applied, _ = _blueprint_versions()
    out = []
    for parsed in applied:
        for wf in parsed.get("workflows") or []:
            steps = normalize_steps(wf["steps"])
            out.append({"blueprint": parsed["metadata"]["name"], "version": parsed["metadata"].get("version"), "id": wf["id"],
                        "steps": steps, "gates": sum(1 for s in steps if s["kind"] == "human_gate"),
                        "agents": {a: v for a, v in _wf_agents(parsed).items() if a in agents_used(steps)},
                        "readiness": _wf_readiness(parsed, steps)})
    return out


class WorkflowStart(BaseModel):
    blueprint: str
    workflow_id: str
    instance_id: str
    input: str = Field(..., min_length=3, max_length=4000)
    case: Optional[str] = Field(None, max_length=64)
    zone: Optional[str] = None  # where artifacts and the Decision Room live
    # auto: the instance's Kanban board when it is there and dispatching, otherwise one Hermes run per agent step
    executor: Literal["auto", "runs", "kanban"] = "auto"


def _wf_kanban(inst: Optional[dict]) -> dict:
    """What the instance's agent last said about Kanban (capability report, refreshed every heartbeat)."""
    return ((inst or {}).get("report") or {}).get("kanban") or {"available": False, "dispatching": False,
                                                                  "why": "the agent has not reported Kanban (update it)"}


def _wf_escalate() -> None:
    """Escalate every overdue gate once, and tell whom it escalated to. Called from heartbeats and reads."""
    for run in store.list_workflow_runs(active=True):
        fresh: list[dict] = []
        store.update_workflow_run(run["id"], lambda d: fresh.extend(escalate_due(d)))
        for step in fresh:
            target = step.get("escalate_to")
            people = [u for u in store.list_users() if target in (u["role"], u["email"])] if target else []
            store.record("fleetcontrol", "workflow.gate_escalated", run["id"],
                         f"step {step['index'] + 1}: {step['timeout']} passed; " + (f"escalated to {target}" if people
                                                                                     else f"no role or account {target!r} to escalate to"))
            _notify_people("gate.escalated", key=f"wf:{run['id']}:gate{step['index']}:escalated", people=people,
                           ctx={"case": run.get("case"), "link": f"/workflow-runs/{run['id']}",
                                "detail": f"{run['workflow_id']}: the {step['role']} approval is overdue ({step['timeout']})."})


def _wf_advance(run_id: str) -> Optional[dict]:
    """Move a run forward as far as it can go: start the next step, queue its agents, open its room."""
    for _ in range(len((store.get_workflow_run(run_id) or {}).get("steps") or []) + 1):
        todo: dict[str, Any] = {}

        def step_forward(d: dict) -> None:
            if d["status"] not in ("running", "waiting"):
                return
            cur = current_step(d)
            if cur is None:
                settle(d)
                return
            if cur["status"] == "pending":
                start_step(cur, time.time())
                todo["started"] = cur["index"]
            d["status"] = "waiting" if cur["kind"] == "human_gate" else "running"
            if cur["kind"] in ("agent", "parallel") and d.get("executor") == "kanban":
                # Kanban runs the whole stretch up to the next gate: submit it once, as linked tasks
                if any(m["agent"] not in (cur.get("tasks") or {}) for m in cur["members"]):
                    stretch = segment(d, cur["index"])
                    for s in stretch:
                        s.setdefault("tasks", {})
                        for m in s["members"]:
                            s["tasks"][m["agent"]] = "submitting"
                    todo["kanban"] = [s["index"] for s in stretch]
            elif cur["kind"] in ("agent", "parallel"):
                members = [m for m in pending_members(cur) if m["agent"] not in cur["jobs"]]
                for m in members:
                    cur["jobs"][m["agent"]] = "queued"  # claimed here, so a concurrent advance cannot queue it twice
                todo["agents"] = [(cur["index"], m) for m in members]
            elif cur["kind"] == "decision_room" and not cur.get("room_id"):
                cur["room_id"] = "pending"
                todo["room"] = cur["index"]
            d["updated_at"] = time.time()

        run = store.update_workflow_run(run_id, step_forward)
        if not run or not todo:
            return run
        parsed = (store.get_blueprint(run["blueprint"], run["version"]) or {}).get("parsed") or {}
        agents = _wf_agents(parsed)
        if todo.get("kanban"):
            stretch = [run["steps"][i] for i in todo["kanban"]]
            first = stretch[0]
            tasks = kanban_tasks(run, stretch, {m["agent"]: compose_input(run, m, first) for m in first["members"]},
                                 lambda m, s: wf_instructions(run, m, s))
            for t in tasks:
                t.update(assignee=agents.get(t["agent"], {}).get("profile", t["agent"]), max_runtime_seconds=WF_STEP_TIMEOUT)
            store.enqueue_job(run["instance_id"], "kanban_submit", {"board": run["board"], "tasks": tasks},
                              {"workflow_run": run_id, "kanban": "submit"})
            store.record("fleetcontrol", "workflow.kanban_submitted", run_id,
                         f"steps {todo['kanban'][0] + 1}–{todo['kanban'][-1] + 1}: {len(tasks)} task(s) on board {run['board']}")
            return run
        for index, member in todo.get("agents", []):
            step = run["steps"][index]
            job = store.enqueue_job(run["instance_id"], "hermes_run",
                                    {"profile": agents.get(member["agent"], {}).get("profile", member["agent"]),
                                     "input": compose_input(run, member, step), "instructions": wf_instructions(run, member, step),
                                     "timeout": WF_STEP_TIMEOUT, "transcript": True},
                                    {"workflow_run": run_id, "step": index, "agent": member["agent"]})
            store.update_workflow_run(run_id, lambda d, i=index, a=member["agent"], j=job["id"]: d["steps"][i]["jobs"].__setitem__(a, j))
        if "started" in todo and run["steps"][todo["started"]]["kind"] == "human_gate":
            gate = run["steps"][todo["started"]]
            store.record("fleetcontrol", "workflow.gate_waiting", run_id, f"step {gate['index'] + 1}: {gate['role']} to approve")
            _notify_people("gate.waiting", key=f"wf:{run_id}:gate{gate['index']}:{gate['started_at']}",
                           people=[u for u in store.list_users() if u["role"] == gate["role"]],
                           ctx={"case": run.get("case"), "link": f"/workflow-runs/{run_id}",
                                "detail": f"{run['workflow_id']} step {gate['index'] + 1}; decide within {gate['timeout']}."})
            return run
        if "room" in todo:
            _wf_open_room(run, todo["room"])
            continue  # the room step is done: carry on
        if not todo.get("agents"):
            continue
        return run
    return store.get_workflow_run(run_id)


def _wf_open_room(run: dict, index: int) -> None:
    """The run's last word goes to people: a Decision Room with every artifact as evidence."""
    step = run["steps"][index]
    zone = run.get("zone")
    if not zone or not store.get_zone(zone):
        def fail(d: dict) -> None:
            s = d["steps"][index]
            s.update(status="failed", error="no content zone for the room: start the run with a zone", finished_at=time.time())
            d.update(status="failed", error=s["error"], finished_at=time.time())
        store.update_workflow_run(run["id"], fail)
        return
    question = question_for(run, step.get("question_template"), {"case": run.get("case") or "this case", "workflow": run["workflow_id"],
                                                                     "input": (run.get("input") or "")[:120]})
    question = question[:300]
    body = RoomCreate(question=question if len(question) >= 10 else f"Approve the outcome of {run['workflow_id']}?", zone=zone,
                      options=WF_ROOM_OPTIONS, case=run.get("case"))
    room = _open_room(body, opened_by=f"workflow:{run['workflow_id']}", kind="agent", actor="fleetcontrol", instance_id=run["instance_id"])
    for name, art in run["artifacts"].items():
        if art.get("artifact_id"):
            item = new_evidence(kind="output", label=f"{name} (by {art['agent']})", ref=art["artifact_id"], source=f"workflow run {run['id']}",
                                added_by="fleetcontrol", at=time.time(), basis="interpretation")
            store.update_room(room["id"], lambda r, it=item: r["evidence"].append(it))
    store.update_workflow_run(run["id"], lambda d: (finish_room_step(d, index, room["id"]), settle(d)))
    store.record("fleetcontrol", "workflow.room_opened", run["id"], room["id"])


def _wf_result(job: dict) -> None:
    """An agent step came back: keep its artifact as a fleet output, record it, and move on."""
    meta, result = job["meta"], job.get("result") or {}
    run = store.get_workflow_run(meta["workflow_run"])
    if not run or run["status"] not in ("running", "waiting"):
        return  # cancelled or finished meanwhile
    index, agent = meta["step"], meta["agent"]
    step = run["steps"][index]
    if step["jobs"].get(agent) != job["id"]:
        return  # an answer for an earlier attempt (the step was sent back and asked again)
    ok = job["status"] == "done" and bool(result.get("output"))
    tools = [c.get("name") for c in (result.get("tool_calls") or [])] if "tool_calls" in result else None
    _wf_record(run, index, agent, ok=ok, output=result.get("output") if ok else None, error=result.get("error") or "no answer",
               run_ref=result.get("run_id"), tools=tools, session_id=result.get("session_id"))
    _wf_advance(run["id"])


def _wf_record(run: dict, index: int, agent: str, *, ok: bool, output: Optional[str], error: Optional[str],
               run_ref: Optional[str] = None, tools: Optional[list] = None, session_id: Optional[str] = None,
               task_id: Optional[str] = None) -> None:
    """One agent's result, from either executor: keep the artifact as a fleet output, file it in the run."""
    step = run["steps"][index]
    artifact_id = None
    member = next((m for m in step["members"] if m["agent"] == agent), {})
    if ok and run.get("zone") and store.get_zone(run["zone"]):
        out = new_output(output_id="out_" + uuid.uuid4().hex[:10], zone=run["zone"], name=member.get("artifact") or f"{agent} result",
                         kind="markdown", classification="confidential", produced_by=agent, at=time.time(), text=output[:200_000],
                         case=run.get("case"), instance_id=run["instance_id"], blueprint=run["blueprint"],
                         source={"kind": "agent", "workflow_run": run["id"], "step": index + 1, "run_id": run_ref,
                                 **({"kanban_task": task_id} if task_id else {})})
        store.save_output(out)
        artifact_id = out["id"]

    def file(d: dict) -> None:
        if agent in d["steps"][index]["results"]:
            return  # already filed (a second read of the same Kanban task)
        record_result(d, index, agent, ok=ok, output=output, artifact_id=artifact_id, run_ref=run_ref, error=None if ok else error)
        d["steps"][index]["results"][agent].update(tools=tools, session_id=session_id, kanban_task=task_id)

    store.update_workflow_run(run["id"], file)
    store.record("fleetcontrol", "workflow.step_done" if ok else "workflow.step_failed", run["id"],
                 f"step {index + 1} {agent}" + ("" if ok else f": {(error or 'no answer')[:160]}"))


def _wf_kanban_submitted(job: dict) -> None:
    """The agent made the tasks: remember which task is whose. If it could not, the run fails and says why."""
    run_id, result = job["meta"]["workflow_run"], job.get("result") or {}
    ids = result.get("ids") or {}

    def file(d: dict) -> None:
        if job["status"] != "done":
            for s in d["steps"]:
                for a, t in list((s.get("tasks") or {}).items()):
                    if t == "submitting":
                        del s["tasks"][a]
            err = f"Kanban refused the tasks: {(result.get('error') or 'no answer')[:300]}"
            d.update(status="failed", error=err, finished_at=time.time())
            return
        for key, task_id in ids.items():
            index, agent, attempt = key.split(":")
            s = d["steps"][int(index)]
            if str(s.get("attempt", 0)) == attempt and (s.get("tasks") or {}).get(agent) == "submitting":
                s["tasks"][agent] = task_id

    run = store.update_workflow_run(run_id, file)
    if run and run["status"] == "failed":
        store.record("fleetcontrol", "workflow.failed", run_id, run["error"])


def _wf_kanban_sync(instance_id: str) -> None:
    """Ask the agent where this instance's Kanban tasks stand (one read in flight per run). Called on heartbeats."""
    for run in store.list_workflow_runs(active=True):
        if run.get("executor") != "kanban" or run["instance_id"] != instance_id:
            continue
        pending = [t for s in run["steps"] for a, t in (s.get("tasks") or {}).items()
                   if t not in (None, "submitting") and a not in (s.get("results") or {})]
        busy = run.get("sync_job") and (store.get_job(run["sync_job"]) or {}).get("status") in ("queued", "running")
        if not pending or busy:
            continue
        job = store.enqueue_job(instance_id, "kanban_read", {"board": run["board"], "task_ids": pending},
                                {"workflow_run": run["id"], "kanban": "read"})
        store.update_workflow_run(run["id"], lambda d, j=job["id"]: d.update(sync_job=j))


def _wf_kanban_read(job: dict) -> None:
    """Kanban tasks that settled become the run's results; the run moves on."""
    run = store.get_workflow_run(job["meta"]["workflow_run"])
    if not run or run["status"] not in ("running", "waiting") or job["status"] != "done":
        return
    tasks = (job.get("result") or {}).get("tasks") or {}
    for s in run["steps"]:
        for agent, task_id in (s.get("tasks") or {}).items():
            if task_id not in tasks or agent in (s.get("results") or {}):
                continue
            settled = kanban_outcome(tasks[task_id])
            if settled:
                ok, output, error = settled
                _wf_record(run, s["index"], agent, ok=ok, output=output, error=error, task_id=task_id)
                run = store.get_workflow_run(run["id"])
    if run["status"] == "failed":
        _wf_kanban_clear(run, "the workflow run failed")  # its later tasks would otherwise wait on the board forever
        return
    _wf_advance(run["id"])


def _wf_kanban_clear(run: dict, why: str) -> None:
    """Archive a run's Kanban tasks that have no result yet (running workers are reclaimed first)."""
    open_tasks = [t for s in run["steps"] for a, t in (s.get("tasks") or {}).items()
                  if t not in (None, "submitting") and a not in (s.get("results") or {})]
    if open_tasks and run.get("board"):
        store.enqueue_job(run["instance_id"], "kanban_cancel", {"board": run["board"], "task_ids": open_tasks},
                          {"workflow_run": run["id"], "kanban": "cancel"})
        store.record("fleetcontrol", "workflow.kanban_cleared", run["id"], f"{len(open_tasks)} task(s): {why}")


@app.post("/api/v1/workflows/runs", status_code=201)
def start_workflow(body: WorkflowStart, user: dict = Depends(require("workflows.run"))) -> dict:
    applied, _ = _blueprint_versions()
    parsed = next((p for p in applied if p["metadata"]["name"] == body.blueprint), None)
    if not parsed:
        raise HTTPException(404, f"no applied blueprint {body.blueprint}: workflows run from what is applied")
    wf = next((w for w in parsed.get("workflows") or [] if w["id"] == body.workflow_id), None)
    if not wf:
        raise HTTPException(404, f"no workflow {body.workflow_id} in {body.blueprint}")
    inst = _require_instance(body.instance_id)
    if inst["mode"] != "agent" or not inst.get("agent_version"):
        raise HTTPException(409, f"{inst['id']} has no paired Fleet Control Agent")
    try:
        steps = normalize_steps(wf["steps"])
    except WorkflowError as exc:
        raise HTTPException(422, str(exc))
    ready = next((r for r in _wf_readiness(parsed, steps) if r["instance_id"] == inst["id"]), None)
    if not ready or not ready["ready"]:
        raise HTTPException(409, "cannot run here: " + "; ".join((ready or {}).get("problems") or ["no paired agent"]))
    needs_room = any(s["kind"] == "decision_room" for s in steps)
    zone = body.zone
    if zone and (not store.get_zone(zone) or zone not in _my_zones(user)):
        raise HTTPException(404, "no such content zone")
    if needs_room and not zone:
        raise HTTPException(422, "this workflow opens a Decision Room: choose the content zone it lives in")
    kanban = _wf_kanban(inst)
    if body.executor == "kanban" and not kanban.get("dispatching"):
        raise HTTPException(409, f"Kanban cannot run it on {inst['id']}: {kanban.get('why') or 'not available'}")
    executor = "kanban" if body.executor == "kanban" or (body.executor == "auto" and kanban.get("dispatching")) else "runs"
    run = new_run(run_id="wfr_" + uuid.uuid4().hex[:10], blueprint=body.blueprint, version=parsed["metadata"]["version"],
                  workflow_id=wf["id"], instance_id=inst["id"], steps=wf["steps"], started_by=user["email"], at=time.time(),
                  zone=zone, case=body.case, input_text=body.input, executor=executor)
    store.save_workflow_run(run)
    store.record(user["email"], "workflow.started", run["id"], f"{body.blueprint} {wf['id']} on {inst['id']} ({executor})")
    return _wf_advance(run["id"])


@app.get("/api/v1/workflows/runs")
def list_workflow_runs(active: bool = False, user: dict = Depends(require("blueprints.read"))) -> list[dict]:
    _wf_escalate()
    return [wf_run_row(r) for r in store.list_workflow_runs(active=active)]


def _wf_run_out(run: dict, user: dict) -> dict:
    gates = {s["index"]: may_decide_gate(s, email=user["email"], role=user["role"]) for s in run["steps"] if s["kind"] == "human_gate"}
    return {**run, "row": wf_run_row(run), "may_decide": {i: {"allowed": ok, "why": why} for i, (ok, why) in gates.items()}}


@app.get("/api/v1/workflows/waiting-for-me")
def workflow_gates_for_me(user: dict = Depends(current_user)) -> list[dict]:
    """Runs stopped at a gate this person may decide now (its role, an Admin, or whom it escalated to)."""
    _wf_escalate()
    out = []
    for run in store.list_workflow_runs(active=True):
        cur = current_step(run)
        if cur and cur["kind"] == "human_gate" and may_decide_gate(cur, email=user["email"], role=user["role"])[0]:
            out.append({**wf_run_row(run), "gate": {"index": cur["index"], "role": cur["role"], "timeout": cur["timeout"],
                                                    "started_at": cur["started_at"], "escalated_at": cur.get("escalated_at")}})
    return out


@app.get("/api/v1/workflows/runs/{run_id}")
def get_workflow_run(run_id: str, user: dict = Depends(current_user)) -> dict:
    """A run with its steps and results. Anyone signed in may read a run whose gate they are asked to decide."""
    run = store.get_workflow_run(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    if not allowed(user["role"], "blueprints.read") and not any(
            s["kind"] == "human_gate" and may_decide_gate(s, email=user["email"], role=user["role"])[0] for s in run["steps"]):
        raise HTTPException(403, "only people who read blueprints, or who decide one of its gates, see a run")
    _wf_escalate()
    return _wf_run_out(store.get_workflow_run(run_id), user)


class GateDecision(BaseModel):
    approve: bool
    note: str = Field("", max_length=2000)


@app.post("/api/v1/workflows/runs/{run_id}/gates/{index}")
def decide_workflow_gate(run_id: str, index: int, body: GateDecision, user: dict = Depends(current_user)) -> dict:
    """Approve a gate, or send the work back (with a note) to the agent the gate names."""
    if not body.approve and not body.note.strip():
        raise HTTPException(422, "say what should change when you send work back")
    errors: list[str] = []

    def change(d: dict) -> None:
        try:
            decide_gate(d, index, by=user["email"], role=user["role"], approve=body.approve, note=body.note)
            if d["status"] == "waiting":
                d["status"] = "running"
        except (WorkflowError, IndexError) as exc:
            errors.append(str(exc) or "no such step")

    run = store.update_workflow_run(run_id, change)
    if not run:
        raise HTTPException(404, "no such run")
    if errors:
        raise HTTPException(409, errors[0])
    store.record(user["email"], "workflow.gate_approved" if body.approve else "workflow.gate_sent_back", run_id,
                 f"step {index + 1}" + (f": {body.note.strip()[:160]}" if body.note.strip() else ""))
    return _wf_run_out(_wf_advance(run_id) or run, user)


@app.post("/api/v1/workflows/runs/{run_id}/cancel")
def cancel_workflow_run(run_id: str, user: dict = Depends(require("workflows.run"))) -> dict:
    run = store.get_workflow_run(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    if run["status"] not in ("running", "waiting"):
        raise HTTPException(409, f"this run is {run['status']}")
    for s in run["steps"]:
        for job_id in (s.get("jobs") or {}).values():
            job = store.get_job(job_id) if job_id and job_id != "queued" else None
            if job and job["status"] in ("queued", "running"):
                store.complete_job(job["id"], {"ok": False, "error": f"run cancelled by {user['email']}"}, instance_id=job["instance_id"])
    _wf_kanban_clear(run, f"run cancelled by {user['email']}")  # stop the workers and take the cards off the board
    run = store.update_workflow_run(run_id, lambda d: d.update(status="cancelled", finished_at=time.time(), updated_at=time.time(),
                                                                error=f"cancelled by {user['email']}"))
    store.record(user["email"], "workflow.cancelled", run_id)
    return _wf_run_out(run, user)


# ----------------------------------------------------------------------------- Integrations (Slice 3)


@app.get("/api/v1/integrations")
def list_integrations(user: dict = Depends(require("instances.read"))) -> dict:
    """MCP servers and model providers across the estate, with health, tools and which agents may use them."""
    instances = store.list_instances()
    live = {i["id"]: store.live_state_for(i["id"]) or {} for i in instances}
    probes = store.integrations()
    # who uses a server is what is applied, not the newest draft; a newer draft only shows as "planned"
    applied, drafts = _blueprint_versions()
    rows = aggregate(instances=instances, live=live, discovered={k: v["servers"] for k, v in probes.items()},
                     blueprints=applied, drafts=drafts, live_at=store.live_state_at(),
                     discovered_at={k: v["profile_at"] for k, v in probes.items()})
    return {"integrations": rows,
            "discovery": [{"instance_id": i["id"], "at": probes.get(i["id"], {}).get("at"),
                           "can_discover": i["mode"] == "agent" and bool(i.get("agent_version")) and bool(live[i["id"]])}
                          for i in instances]}


class DiscoverBody(BaseModel):
    instance_id: str
    probe: bool = True


@app.post("/api/v1/integrations/discover")
def discover_integrations(body: DiscoverBody, user: dict = Depends(require("instances.operate"))) -> dict:
    inst = _require_instance(body.instance_id)
    if inst["mode"] != "agent" or not inst.get("agent_version"):
        raise HTTPException(409, f"{inst['id']} has no paired Fleet Control Agent")
    profiles = sorted(store.live_state_for(inst["id"]) or {})
    if not profiles:
        raise HTTPException(409, f"import live profiles from {inst['id']} first")
    job = store.enqueue_job(inst["id"], "mcp_discover", {"profiles": profiles, "probe": body.probe}, {"discovery": inst["id"]})
    store.record(user["email"], "integrations.discovery_requested", inst["id"], f"{len(profiles)} profiles")
    return {"job_id": job["id"]}


class McpServerBody(BaseModel):
    instance_id: str
    profile: str
    name: str = Field(..., min_length=1, max_length=64)
    url: Optional[str] = None
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    auth: Optional[Literal["none", "oauth"]] = None


@app.post("/api/v1/integrations/mcp", status_code=201)
def add_mcp_server(body: McpServerBody, user: dict = Depends(require("instances.operate"))) -> dict:
    """Add an MCP server to one profile on one instance. Credentials are added on the instance: none pass through
    Fleet Control (and none are stored in the job)."""
    inst = _require_instance(body.instance_id)
    if body.profile not in (store.live_state_for(inst["id"]) or {}):
        raise HTTPException(422, f"no profile {body.profile!r} in the last import from {inst['id']}")
    try:
        config = mcp_config(body.name, url=body.url, command=body.command, args=body.args, auth=body.auth)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    job = store.enqueue_job(inst["id"], "mcp_write", {"profile": body.profile, "action": "add", "config": config},
                            {"discovery": inst["id"], "profiles": [body.profile]})
    store.record(user["email"], "integrations.mcp_added", f"{body.name} on {inst['id']}/{body.profile}",
                 config.get("url") or config.get("command") or "")
    return {"job_id": job["id"]}


class McpChange(BaseModel):
    instance_id: str
    profile: str
    enabled: Optional[bool] = None
    remove: bool = False


@app.patch("/api/v1/integrations/mcp/{server}")
def change_mcp_server(server: str, body: McpChange, user: dict = Depends(require("instances.operate"))) -> dict:
    inst = _require_instance(body.instance_id)
    if body.remove:
        action = "remove"
    elif body.enabled is None:
        raise HTTPException(422, "say what to change: enabled, or remove")
    else:
        action = "enable" if body.enabled else "disable"
    job = store.enqueue_job(inst["id"], "mcp_write", {"profile": body.profile, "action": action, "server": server},
                            {"discovery": inst["id"], "profiles": [body.profile]})
    store.record(user["email"], f"integrations.mcp_{action}d", f"{server} on {inst['id']}/{body.profile}")
    return {"job_id": job["id"]}


def _discovery_result(job: dict, instance_id: str) -> None:
    """An mcp_discover result is the instance's new picture; an mcp_write is followed by a fresh discovery."""
    result = job.get("result") or {}
    if job["kind"] == "mcp_discover" and job["status"] == "done":
        # a discovery after an mcp_write covers one profile: merge it, never let it replace the others
        live = store.live_state_for(instance_id)
        store.save_integrations(instance_id, result.get("servers") or {}, result.get("at") or time.time(),
                                covered=(job.get("params") or {}).get("profiles"), keep=set(live) if live else None)
    elif job["kind"] == "mcp_write" and job["status"] == "done":
        profiles = job["meta"].get("profiles") or sorted(store.live_state_for(instance_id) or {})
        store.enqueue_job(instance_id, "mcp_discover", {"profiles": profiles, "probe": True}, {"discovery": instance_id})


# ----------------------------------------------------------------------------- Test Lab and Assurance (Slice 3)


def _test_rows(bp: Blueprint) -> list[dict]:
    """A blueprint's tests with what they target: agent tests run on that agent's profile; workflow tests wait for Workflows."""
    agents = {a.id: a for a in bp.agents}
    return [{"test": t.model_dump(mode="json"), "target_kind": "agent" if t.target in agents else "workflow",
             "profile": agents[t.target].profile_name if t.target in agents else None} for t in bp.tests]


def _latest_runs(name: str, version: int) -> dict[str, dict]:
    """The newest finished-or-running run of each test of one blueprint version.

    A cancelled run is skipped: someone stopping a run says nothing about the test, so the test keeps
    showing the last result it actually produced rather than a failure it never had."""
    latest: dict[str, dict] = {}
    for r in store.list_test_runs(blueprint=name, version=version, limit=2000):
        if r.get("status") == "cancelled":
            continue
        latest.setdefault(r["test_id"], r)
    return latest


def _run_summary(r: dict) -> dict:
    failure = next((c["detail"] for c in r.get("checks") or [] if c["outcome"] == "fail"), None)
    return {"id": r["id"], "status": r["status"], "instance_id": r["instance_id"], "created_at": r["created_at"],
            "finished_at": r.get("finished_at"), "failure": (f"{failure}") if failure else r.get("error")}


@app.get("/api/v1/testlab/suites")
def testlab_suites(user: dict = Depends(require("blueprints.read"))) -> list[dict]:
    """Every blueprint's latest version with its tests, grouped by what they target, and each test's last run."""
    out, applied, gate = [], store.all_applied(), bool(_settings()["require_tests_for_production"])
    for name, versions in store.all_blueprints().items():
        version = max(versions)
        bp = Blueprint.model_validate(versions[version]["parsed"])
        latest = _latest_runs(name, version)
        tests = [{**row, "last_run": _run_summary(latest[row["test"]["id"]]) if row["test"]["id"] in latest else None} for row in _test_rows(bp)]
        out.append({"blueprint": name, "version": version, "status": versions[version]["status"],
                    "agents": [{"id": a.id, "profile": a.profile_name, "role": a.role} for a in bp.agents],
                    "workflows": [w.id for w in bp.workflows], "tests": tests,
                    "gates_production": gate and any(t["target_kind"] == "agent" for t in tests),
                    "applied_on": sorted(iid for iid, a in applied.items() if a["name"] == name)})
    return out


class TestRunStart(BaseModel):
    blueprint: str
    version: int
    instance_id: str
    test_ids: Optional[list[str]] = Field(None, description="Tests to run; default every test of the version.")


@app.post("/api/v1/testlab/runs", status_code=201)
def start_test_runs(body: TestRunStart, user: dict = Depends(require("tests.run"))) -> dict:
    bp = _blueprint(body.blueprint, body.version)
    inst = _require_instance(body.instance_id)
    if inst["environment"] == "production":
        raise HTTPException(409, "tests run on lab or staging instances, never on production")
    if inst["mode"] != "agent" or not inst.get("agent_version"):
        raise HTTPException(409, f"{inst['id']} has no paired Fleet Control Agent to run tests")
    rows = _test_rows(bp)
    if body.test_ids is not None:
        unknown = sorted(set(body.test_ids) - {r["test"]["id"] for r in rows})
        if unknown:
            raise HTTPException(404, f"no such test in {bp.metadata.name} v{bp.metadata.version}: {', '.join(unknown)}")
        rows = [r for r in rows if r["test"]["id"] in set(body.test_ids)]
    if not rows:
        raise HTTPException(409, f"{bp.metadata.name} v{bp.metadata.version} has no tests")
    live = store.live_state_for(inst["id"]) or {}
    started, skipped = [], []
    for row in rows:
        t = row["test"]
        if row["target_kind"] != "agent":
            skipped.append({"test_id": t["id"], "reason": "workflow tests run with Workflows (Slice 5)"})
            continue
        if row["profile"] not in live:
            skipped.append({"test_id": t["id"], "reason": f"profile {row['profile']} is not on {inst['id']}: apply the blueprint there, then import"})
            continue
        # Strictly after this test's previous run: Windows' clock ticks every ~16 ms, and two runs with the same
        # created_at would make "the newest run" (the test's result, and the production gate) a coin toss.
        prev = store.list_test_runs(blueprint=bp.metadata.name, version=bp.metadata.version, test_id=t["id"], limit=1)
        created_at = max(time.time(), prev[0]["created_at"] + 1e-6) if prev else time.time()
        doc = {"id": "tr_" + uuid.uuid4().hex[:10], "blueprint": bp.metadata.name, "version": bp.metadata.version, "test_id": t["id"],
               "target": t["target"], "profile": row["profile"], "instance_id": inst["id"], "environment": inst["environment"],
               "status": "running", "created_at": created_at, "created_by": user["email"], "finished_at": None, "job_id": None,
               "checks": [], "claims": [], "output": None, "tool_calls": None, "usage": None, "duration_s": None, "error": None,
               "evidence": None, "evidence_error": None, "session_id": None, "run_id": None, "notes": []}
        store.save_test_run(doc)
        timeout = min(max(float((t.get("limits") or {}).get("max_seconds") or 60) * 3, 120.0), 900.0)
        # hints: what the test looks for. The real agent ignores them; the demo's simulated agent acts on them.
        params = {"profile": row["profile"], "scenario": t["scenario"], "timeout": timeout,
                  "hints": {"required_tools": t.get("required_tools", []), "forbidden_tools": t.get("forbidden_tools", []),
                            "expected_artifact": t.get("expected_artifact"), "expected": t.get("expected")}}
        job = store.enqueue_job(inst["id"], "run_test", params, {"test_run": doc["id"]})
        store.update_test_run(doc["id"], lambda d, job_id=job["id"]: d.update(job_id=job_id))
        started.append(doc["id"])
    store.record(user["email"], "tests.run_requested", f"{bp.metadata.name} v{bp.metadata.version} on {inst['id']}",
                 f"{len(started)} started" + (f", {len(skipped)} skipped" if skipped else ""))
    return {"runs": started, "skipped": skipped}


@app.get("/api/v1/testlab/runs")
def list_test_runs(blueprint: Optional[str] = None, test_id: Optional[str] = None, instance_id: Optional[str] = None,
                   limit: int = 50, user: dict = Depends(require("blueprints.read"))) -> list[dict]:
    return store.list_test_runs(blueprint=blueprint, test_id=test_id, instance_id=instance_id, limit=max(1, min(limit, 500)))


@app.get("/api/v1/testlab/runs/{run_id}")
def get_test_run(run_id: str, user: dict = Depends(require("blueprints.read"))) -> dict:
    run = store.get_test_run(run_id)
    if not run:
        raise HTTPException(404, "no such test run")
    return run


@app.post("/api/v1/testlab/runs/{run_id}/cancel")
def cancel_test_run(run_id: str, user: dict = Depends(require("tests.run"))) -> dict:
    """Stop a run that is still going. It becomes ``cancelled`` — not ``error`` — because someone
    stopping a run says nothing about whether the agent would have passed. Its job is closed so the
    agent does not deliver it later, and a result that arrives anyway is ignored (``_test_result``)."""
    doc = store.get_test_run(run_id)
    if not doc:
        raise HTTPException(404, "no such test run")
    if doc.get("status") not in ("running", "queued"):
        raise HTTPException(409, f"this run already finished ({doc.get('status')})")
    now = time.time()
    reason = "cancelled by " + user["email"]

    def change(d: dict) -> None:
        d.update(status="cancelled", error=reason, finished_at=now, checks=[], claims=[])

    updated = store.update_test_run(run_id, change)
    job = store.get_job(doc["job_id"]) if doc.get("job_id") else None
    if job and job.get("status") in ("queued", "running"):
        store.complete_job(job["id"], {"ok": False, "error": reason}, instance_id=job["instance_id"])
    store.record(user["email"], "test.cancelled", f"{doc['blueprint']} v{doc['version']} {doc['test_id']}",
                 f"{run_id} on {doc['instance_id']}")
    return updated


@app.delete("/api/v1/testlab/runs/{run_id}")
def delete_test_run(run_id: str, user: dict = Depends(require("tests.manage"))) -> dict:
    """Remove a finished run and the assurance claims it carried. Admin only, and never a run that is
    still going — cancel it first, so a result cannot arrive for a run that no longer exists."""
    doc = store.get_test_run(run_id)
    if not doc:
        raise HTTPException(404, "no such test run")
    if doc.get("status") in ("running", "queued"):
        raise HTTPException(409, "this run is still going: cancel it first")
    store.delete_test_run(run_id)
    store.record(user["email"], "test.run_deleted", f"{doc['blueprint']} v{doc['version']} {doc['test_id']}",
                 f"{run_id} on {doc['instance_id']}, was {doc.get('status')}")
    return {"ok": True, "id": run_id}


def _test_result(job: dict) -> None:
    """A run_test job came back: judge it against the test and file the checks and claims."""
    run_id = job["meta"]["test_run"]
    doc = store.get_test_run(run_id)
    if not doc:
        return
    if doc.get("status") == "cancelled":
        return  # someone stopped this run; a result arriving afterwards must not bring it back to life
    result = job.get("result") or {}
    if job["status"] != "done":
        verdict = {"status": "error", "claims": [], "checks": [{"id": "run", "kind": "run", "subject": "the run", "outcome": "fail",
                                                                 "detail": result.get("error") or "the agent could not run the test"}]}
    else:
        rec = store.get_blueprint(doc["blueprint"], doc["version"])
        bp = Blueprint.model_validate(rec["parsed"]) if rec else None
        test = next((t.model_dump(mode="json") for t in bp.tests if t.id == doc["test_id"]), None) if bp else None
        agent = next((a.model_dump(mode="json") for a in bp.agents if a.id == doc["target"]), None) if bp else None
        verdict = evaluate(test, agent, result) if test else {
            "status": "error", "claims": [],
            "checks": [{"id": "run", "kind": "run", "subject": "the test", "outcome": "fail", "detail": "the test is no longer in the blueprint"}]}
    calls = result.get("tool_calls")

    def file(d: dict) -> None:
        d.update(status=verdict["status"], checks=verdict["checks"], claims=verdict["claims"], finished_at=time.time(),
                 output=(result.get("output") or "")[:4000] or None, tool_calls=None if calls is None else calls[:100],
                 usage=result.get("usage"), duration_s=result.get("duration_s"), error=result.get("error"),
                 evidence=result.get("evidence"), evidence_error=result.get("evidence_error"),
                 session_id=result.get("session_id"), run_id=result.get("run_id"), notes=result.get("notes") or [])

    store.update_test_run(run_id, file)
    store.record("fleetcontrol", f"test.{verdict['status']}", run_id,
                 f"{doc['blueprint']} v{doc['version']} {doc['test_id']} on {doc['instance_id']}")
    for verdict_name, event in (("No evidence", "assurance.no_evidence"), ("Policy blocked", "assurance.policy_blocked")):
        claims = [c for c in verdict["claims"] if c.get("verdict") == verdict_name]
        if claims:
            ctx = {"instance": doc["instance_id"], "link": "/assurance",
                   "detail": f"{doc['test_id']} ({doc['blueprint']} v{doc['version']}): {len(claims)} claim(s) {verdict_name}."}
            _notify(event, key=f"run:{run_id}:{event}", ctx=ctx)
            if event == "assurance.no_evidence":
                _notify_people(event, key=f"run:{run_id}:{event}", ctx=ctx, people=_people_with("assurance.read"))


class TestEdit(BaseModel):
    target: Optional[str] = None
    scenario: Optional[str] = None
    required_tools: Optional[list[str]] = None
    forbidden_tools: Optional[list[str]] = None
    expected_artifact: Optional[str] = None
    evaluator: Optional[Literal["schema", "exact", "contains", "artifact-exists", "none"]] = None
    expected: Optional[str] = None
    limits: Optional[dict[str, Any]] = None


def _draft(name: str, version: int) -> dict:
    rec = store.get_blueprint(name, version)
    if not rec:
        raise HTTPException(404, "unknown blueprint version")
    if rec["status"] != "draft":
        raise HTTPException(409, f"{name} v{version} is {rec['status']} and immutable; create a draft to edit it")
    return rec


@app.put("/api/v1/blueprints/{name}/{version}/tests/{test_id}")
def save_test(name: str, version: int, test_id: str, body: TestEdit, user: dict = Depends(require("blueprints.write"))) -> dict:
    rec = _draft(name, version)
    try:
        new, created = upsert_test(Blueprint.model_validate(rec["parsed"]), test_id, body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    store.update_blueprint(name, version, dump_blueprint(new), new.model_dump(mode="json"), user["email"])
    store.record(user["email"], "blueprint.test_saved", f"{name} v{version}", f"{test_id} ({'new' if created else 'changed'})")
    return {"name": name, "version": version, "created": created,
            "test": next(t for t in new.tests if t.id == test_id).model_dump(mode="json")}


@app.delete("/api/v1/blueprints/{name}/{version}/tests/{test_id}")
def delete_test(name: str, version: int, test_id: str, user: dict = Depends(require("blueprints.write"))) -> dict:
    rec = _draft(name, version)
    try:
        new = remove_test(Blueprint.model_validate(rec["parsed"]), test_id)
    except KeyError:
        raise HTTPException(404, f"no test {test_id!r} in {name} v{version}")
    store.update_blueprint(name, version, dump_blueprint(new), new.model_dump(mode="json"), user["email"])
    store.record(user["email"], "blueprint.test_removed", f"{name} v{version}", test_id)
    return {"ok": True}


def _preflight(plan: dict) -> dict:
    """The test gate for a plan: every agent test of the blueprint version, with its latest result on lab or staging.
    Production applies need them all passed (Settings → Approvals); workflow tests wait for Workflows."""
    name, version = plan["blueprint"]["name"], plan["blueprint"]["version"]
    rec = store.get_blueprint(name, version)
    rows = _test_rows(Blueprint.model_validate(rec["parsed"])) if rec else []
    latest = _latest_runs(name, version)
    tests = []
    for row in rows:
        if row["target_kind"] != "agent":
            continue
        r = latest.get(row["test"]["id"])
        tests.append({"test_id": row["test"]["id"], "status": r["status"] if r else "not_run",
                      "instance_id": r["instance_id"] if r else None, "at": (r.get("finished_at") or r["created_at"]) if r else None})
    passed = sum(1 for t in tests if t["status"] == "passed")
    required = plan["environment"] == "production" and bool(tests) and bool(_settings()["require_tests_for_production"])
    return {"total": len(tests), "passed": passed, "tests": tests,
            "deferred": [row["test"]["id"] for row in rows if row["target_kind"] != "agent"],
            "required": required, "satisfied": (not required) or passed == len(tests)}


@app.get("/api/v1/plans/{plan_id}/preflight")
def plan_preflight(plan_id: str, user: dict = Depends(require("plans.read"))) -> dict:
    plan = store.get_plan(plan_id)
    if not plan:
        raise HTTPException(404)
    return _preflight(plan)


def _claim_rows(since: Optional[float], verdict: Optional[str] = None, instance_id: Optional[str] = None) -> list[dict]:
    """Every claim of every finished test run since ``since``, newest run first."""
    rows = []
    for r in store.list_test_runs(instance_id=instance_id, since=since, limit=5000):
        if r["status"] == "running":
            continue
        for n, c in enumerate(r.get("claims") or []):
            if verdict and c["verdict"] != verdict:
                continue
            rows.append({"id": f"{r['id']}:{n}", "run_id": r["id"], "at": r.get("finished_at") or r["created_at"],
                         "instance_id": r["instance_id"], "blueprint": r["blueprint"], "version": r["version"],
                         "test_id": r["test_id"], "agent": r["target"], "claim": c["claim"], "verdict": c["verdict"],
                         "detail": c["detail"], "check": c.get("check")})
    return rows


@app.get("/api/v1/assurance/claims")
def assurance_claims(verdict: Optional[str] = None, instance_id: Optional[str] = None, hours: int = 24 * 7,
                     user: dict = Depends(require("assurance.read"))) -> list[dict]:
    if verdict and verdict not in VERDICTS:
        raise HTTPException(422, f"verdict must be one of: {', '.join(VERDICTS)}")
    return _claim_rows(time.time() - max(1, min(hours, 24 * 366)) * 3600, verdict, instance_id)


@app.get("/api/v1/assurance/summary")
def assurance_summary(user: dict = Depends(require("assurance.read"))) -> dict:
    rows = _claim_rows(time.time() - 86400)
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    by_instance: dict[str, int] = {}
    for r in rows:
        if r["verdict"] == "Not verifiable":
            by_instance[r["instance_id"]] = by_instance.get(r["instance_id"], 0) + 1
    return {"hours": 24, "total": len(rows), "counts": counts, "instances": len({r["instance_id"] for r in rows}),
            "most_not_verifiable": max(by_instance, key=by_instance.get) if by_instance else None}


@app.get("/api/v1/assurance/export")
def assurance_export(user: dict = Depends(require("assurance.read"))) -> Response:
    import csv
    import io

    rows = _claim_rows(time.time() - 366 * 86400)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time_utc", "instance", "blueprint", "version", "test", "agent", "claim", "verdict", "detail", "test_run"])
    for r in rows:
        w.writerow([time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["at"])), r["instance_id"], r["blueprint"], r["version"],
                    r["test_id"], r["agent"], r["claim"], r["verdict"], r["detail"], r["run_id"]])
    store.record(user["email"], "assurance.exported", "assurance evidence", f"{len(rows)} claims")
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="fleetcontrol-assurance.csv"'})


# ----------------------------------------------------------------------------- Fleet Architect (Slice 2)


def _architect_config() -> Optional[dict]:
    cfg = store.get_settings().get("architect")
    return cfg if isinstance(cfg, dict) and cfg.get("instance_id") and cfg.get("profile") else None


def _architect_out() -> dict:
    """The chosen architect (with its model and instance status) and every profile that could be one."""
    candidates = []
    for inst in store.list_instances():
        live = store.live_state_for(inst["id"]) or {}
        if inst["mode"] == "agent" and inst.get("agent_version") and live:
            candidates.append({"instance_id": inst["id"], "environment": inst["environment"],
                               "profiles": [{"name": n, "model": (s.get("model") or {}).get("name")} for n, s in sorted(live.items())]})
    cfg = _architect_config()
    if cfg:
        state = (store.live_state_for(cfg["instance_id"]) or {}).get(cfg["profile"]) or {}
        inst = store.get_instance(cfg["instance_id"])
        cfg = {**cfg, "model": state.get("model"), "instance_status": inst["status"] if inst else "missing"}
    return {"config": cfg, "candidates": candidates}


class ArchitectConfig(BaseModel):
    instance_id: str
    profile: str


@app.get("/api/v1/architect/config")
def get_architect_config(user: dict = Depends(require("blueprints.read"))) -> dict:
    return _architect_out()


@app.put("/api/v1/architect/config")
def set_architect_config(body: ArchitectConfig, user: dict = Depends(require("blueprints.write"))) -> dict:
    inst = _require_instance(body.instance_id)
    if inst["mode"] != "agent" or not inst.get("agent_version"):
        raise HTTPException(409, f"{body.instance_id} has no paired Fleet Control Agent; the architect is asked through it")
    if body.profile not in (store.live_state_for(body.instance_id) or {}):
        raise HTTPException(422, f"no profile {body.profile!r} in the last import from {body.instance_id}; import live profiles first")
    store.set_settings({"architect": {"instance_id": body.instance_id, "profile": body.profile}}, user["email"])
    store.record(user["email"], "architect.configured", f"{body.profile} on {body.instance_id}")
    return _architect_out()


class ArchitectAsset(BaseModel):
    instance_id: str


@app.post("/api/v1/architect/blueprint-asset", status_code=201)
def create_architect_asset(body: ArchitectAsset, user: dict = Depends(require("blueprints.write"))) -> dict:
    """The fleet-control-architect blueprint (one tool-less fc-architect profile) for an instance, to plan and apply."""
    inst = _require_instance(body.instance_id)
    live = store.live_state_for(body.instance_id) or {}
    source = live.get("default") or next(iter(live.values()), None)
    model = (source or {}).get("model") or {}
    if not (model.get("provider") and model.get("name")):
        raise HTTPException(409, f"import live profiles from {body.instance_id} first: the architect uses its default model")
    version = store.next_blueprint_version(ARCHITECT_BLUEPRINT)
    bp = architect_blueprint(model, instance_id=body.instance_id, environment=inst["environment"], owner=user["email"], version=version)
    rec = store.save_blueprint(ARCHITECT_BLUEPRINT, version, dump_blueprint(bp), bp.model_dump(mode="json"), user["email"])
    store.record(user["email"], "blueprint.saved", f"{ARCHITECT_BLUEPRINT} v{version}", f"Fleet Control architect for {body.instance_id}")
    return {"name": rec["name"], "version": rec["version"], "status": rec["status"]}


def _require_architect() -> dict:
    cfg = _architect_config()
    if not cfg:
        raise HTTPException(409, "choose the architect profile first (Fleet Architect → Architect)")
    inst = store.get_instance(cfg["instance_id"])
    if not inst or inst["mode"] != "agent" or not inst.get("agent_version"):
        raise HTTPException(409, f"the architect's instance {cfg['instance_id']} has no paired Fleet Control Agent")
    return cfg


def _architect_session(session_id: str) -> dict:
    doc = store.get_architect_session(session_id)
    if not doc:
        raise HTTPException(404, "no such architect session")
    return doc


def _latest_proposal(doc: dict) -> Optional[dict]:
    return next((v for v in reversed(doc["versions"]) if v["status"] == "ready"), None)


def _session_out(doc: dict) -> dict:
    versions = doc["versions"]
    pending = versions[-1] if versions and versions[-1]["status"] == "running" else None
    if pending:
        job = store.get_job(pending["job_id"]) if pending.get("job_id") else None
        pending = {**pending, "job_status": job["status"] if job else "queued"}
    return {**doc, "latest": _latest_proposal(doc), "pending": pending}


def _ask_architect(session_id: str) -> dict:
    """Queue the next proposal version: a hermes_run job on the architect's instance."""
    doc = _architect_session(session_id)
    cfg = doc["architect"]
    version = len(doc["versions"]) + 1
    latest = _latest_proposal(doc)
    previous = None
    if latest:
        previous = {**latest["proposal"],
                    "agents": [merge_edit(a, doc["edits"].get(a["id"], {})) for a in latest["proposal"]["agents"]]}
    live = {i["id"]: store.live_state_for(i["id"]) or {} for i in store.list_instances()}
    params = {
        "profile": cfg["profile"],
        "input": request_text(doc["mission"], answers=doc["answers"], previous=previous, decisions=doc["decisions"]),
        "instructions": instructions(Constraints(**doc["constraints"]), estate_summary(live)),
        "timeout": 600,
    }
    entry = {"version": version, "job_id": None, "status": "running", "requested_at": time.time(), "finished_at": None,
             "proposal": None, "error": None, "raw": None, "run": None}
    # the version exists before its job does, so a fast result always finds it
    store.update_architect_session(session_id, lambda d: d["versions"].append(entry))
    job = store.enqueue_job(cfg["instance_id"], "hermes_run", params, {"architect_session": session_id, "version": version})

    def set_job(d: dict) -> None:
        for v in d["versions"]:
            if v["version"] == version:
                v["job_id"] = job["id"]

    return store.update_architect_session(session_id, set_job)


def _architect_result(job: dict) -> None:
    """A proposal version's run came back: validate the answer and file it (or the reason it cannot be used)."""
    session_id, version = job["meta"]["architect_session"], job["meta"]["version"]
    result = job.get("result") or {}
    proposal, error = None, None
    if job["status"] == "done":
        try:
            proposal = parse_proposal(result.get("output") or "")
        except ProposalError as exc:
            error = str(exc)
    else:
        error = result.get("error") or f"the run ended {result.get('status') or 'without a result'}"

    def file(d: dict) -> None:
        for v in d["versions"]:
            if v["version"] == version:
                v.update(status="ready" if proposal else "failed", proposal=proposal, error=error, finished_at=time.time(),
                         run={"run_id": result.get("run_id"), "usage": result.get("usage")},
                         raw=None if proposal else (result.get("output") or "")[:4000] or None)
        if proposal:  # decisions and edits carry over for the agents the new version keeps
            ids = {a["id"] for a in proposal["agents"]}
            d["decisions"] = {k: v for k, v in d["decisions"].items() if k in ids}
            d["edits"] = {k: v for k, v in d["edits"].items() if k in ids}

    store.update_architect_session(session_id, file)
    detail = f"v{version}: {len(proposal['agents'])} agents" if proposal else f"v{version}: {(error or '')[:160]}"
    store.record("fleetcontrol", "architect.proposal_ready" if proposal else "architect.proposal_failed", session_id, detail)


class ArchitectStart(BaseModel):
    mission: str = Field(..., min_length=20, max_length=2000)
    constraints: Constraints = Field(default_factory=Constraints)


@app.post("/api/v1/architect/sessions", status_code=201)
def start_architect(body: ArchitectStart, user: dict = Depends(require("blueprints.write"))) -> dict:
    cfg = _require_architect()
    now = time.time()
    doc = {"id": "arc_" + uuid.uuid4().hex[:10], "created_by": user["email"], "created_at": now, "updated_at": now,
           "mission": body.mission.strip(), "constraints": body.constraints.model_dump(),
           "architect": {"instance_id": cfg["instance_id"], "profile": cfg["profile"]},
           "answers": [], "versions": [], "decisions": {}, "edits": {}, "blueprint": None}
    store.save_architect_session(doc)
    doc = _ask_architect(doc["id"])
    store.record(user["email"], "architect.asked", doc["id"], f"v1 to {cfg['profile']} on {cfg['instance_id']}")
    return _session_out(doc)


@app.get("/api/v1/architect/sessions")
def list_architect_sessions(user: dict = Depends(require("blueprints.read"))) -> list[dict]:
    return [{"id": d["id"], "mission": d["mission"], "created_by": d["created_by"], "created_at": d["created_at"],
             "updated_at": d["updated_at"], "versions": len(d["versions"]),
             "status": d["versions"][-1]["status"] if d["versions"] else "new", "blueprint": d.get("blueprint")}
            for d in store.list_architect_sessions()]


@app.get("/api/v1/architect/sessions/{session_id}")
def get_architect_session(session_id: str, user: dict = Depends(require("blueprints.read"))) -> dict:
    return _session_out(_architect_session(session_id))


class ArchitectAnswer(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    answer: str = Field(..., min_length=1, max_length=1000)


class ArchitectAsk(BaseModel):
    answers: list[ArchitectAnswer] = Field(default_factory=list, max_length=10)


@app.post("/api/v1/architect/sessions/{session_id}/ask")
def ask_architect_again(session_id: str, body: ArchitectAsk, user: dict = Depends(require("blueprints.write"))) -> dict:
    doc = _architect_session(session_id)
    if doc["versions"] and doc["versions"][-1]["status"] == "running":
        raise HTTPException(409, f"the architect is still working on proposal v{doc['versions'][-1]['version']}")
    cfg = _require_architect()
    version = len(doc["versions"]) + 1

    def note(d: dict) -> None:
        d["architect"] = {"instance_id": cfg["instance_id"], "profile": cfg["profile"]}
        d["answers"].extend({"question": a.question.strip(), "answer": a.answer.strip(), "version": version} for a in body.answers)

    store.update_architect_session(session_id, note)
    doc = _ask_architect(session_id)
    store.record(user["email"], "architect.asked", session_id,
                 f"v{version}" + (f" with {len(body.answers)} answer(s)" if body.answers else ""))
    return _session_out(doc)


class AgentDecision(BaseModel):
    decision: Optional[Literal["accepted", "removed", "proposed"]] = None  # "proposed" undoes a decision
    edit: Optional[dict[str, Any]] = None


@app.patch("/api/v1/architect/sessions/{session_id}/agents/{agent_id}")
def decide_agent(session_id: str, agent_id: str, body: AgentDecision, user: dict = Depends(require("blueprints.write"))) -> dict:
    doc = _architect_session(session_id)
    latest = _latest_proposal(doc)
    agent = next((a for a in latest["proposal"]["agents"] if a["id"] == agent_id), None) if latest else None
    if not agent:
        raise HTTPException(404, f"no agent {agent_id!r} in the latest proposal")
    merged = None
    if body.edit:
        unknown = sorted(set(body.edit) - EDITABLE)
        if unknown:
            raise HTTPException(422, f"cannot edit {', '.join(unknown)} here; editable: {', '.join(sorted(EDITABLE))}")
        merged = merge_edit(doc["edits"].get(agent_id, {}), body.edit)
        try:
            check_agent(merge_edit(agent, merged))
        except ValueError as exc:
            raise HTTPException(422, f"the edited agent is not valid: {exc}")

    def change(d: dict) -> None:
        if body.decision == "proposed":
            d["decisions"].pop(agent_id, None)
        elif body.decision:
            d["decisions"][agent_id] = body.decision
        if merged is not None:
            d["edits"][agent_id] = merged

    doc = store.update_architect_session(session_id, change)
    what = ", ".join(x for x in (body.decision and ("undone" if body.decision == "proposed" else body.decision),
                                  merged is not None and "edited: " + ", ".join(sorted(body.edit or {}))) if x)
    store.record(user["email"], "architect.agent_decided", f"{session_id}: {agent_id}", what or "no change")
    return _session_out(doc)


class ArchitectSave(BaseModel):
    name: str


@app.post("/api/v1/architect/sessions/{session_id}/blueprint", status_code=201)
def save_architect_blueprint(session_id: str, body: ArchitectSave, user: dict = Depends(require("blueprints.write"))) -> dict:
    doc = _architect_session(session_id)
    latest = _latest_proposal(doc)
    if not latest:
        raise HTTPException(409, "there is no proposal to save yet")
    accepted = [a["id"] for a in latest["proposal"]["agents"] if doc["decisions"].get(a["id"]) == "accepted"]
    if not accepted:
        raise HTTPException(409, "accept at least one agent first")
    if store.blueprint_versions(body.name):
        raise HTTPException(409, f"a blueprint named {body.name!r} already exists; choose another name")
    try:
        bp = to_blueprint(latest["proposal"], name=body.name, owner=user["email"], mission=doc["mission"],
                          constraints=Constraints(**doc["constraints"]), accepted=accepted, edits=doc["edits"])
    except ValueError as exc:
        raise HTTPException(422, f"invalid blueprint: {exc}")
    rec = store.save_blueprint(bp.metadata.name, 1, dump_blueprint(bp), bp.model_dump(mode="json"), user["email"])
    store.update_architect_session(session_id, lambda d: d.update(blueprint={"name": rec["name"], "version": 1}))
    store.record(user["email"], "architect.blueprint_saved", f"{rec['name']} v1",
                 f"from {session_id} proposal v{latest['version']}: {', '.join(accepted)}")
    return {"name": rec["name"], "version": rec["version"], "status": rec["status"], "agents": accepted}


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


class InstanceUpdate(BaseModel):
    """What an instance's record may be changed to. The id is not editable: the agent on the host
    was installed with it, and changing it here would orphan that agent."""
    environment: Optional[str] = Field(None, pattern=r"^(lab|staging|production)$")
    owner: Optional[str] = Field(None, min_length=3, max_length=200)


@app.patch("/api/v1/instances/{instance_id}")
def edit_instance(instance_id: str, body: InstanceUpdate, user: dict = Depends(require("instances.connect"))) -> dict:
    """Change an instance's environment or owner. Moving an instance between environments changes the
    approvals its applies need (Settings → Approvals), so the change is audited with both values."""
    inst = store.get_instance(instance_id)
    if not inst:
        raise HTTPException(404, "no such instance")
    changes = {k: v for k, v in body.model_dump(exclude_none=True).items() if inst.get(k) != v}
    if not changes:
        return _instance_row(inst)
    updated = store.update_instance(instance_id, **changes)
    for field, value in changes.items():
        store.record(user["email"], "instance.updated", instance_id, f"{field}: {inst.get(field)} → {value}")
    return _instance_row(updated)


@app.delete("/api/v1/instances/{instance_id}")
def remove_instance(instance_id: str, user: dict = Depends(require("instances.connect"))) -> dict:
    """Forget an instance: Fleet Control stops tracking it and its agent can no longer report.

    Nothing on the host changes — the Hermes profiles and the agent keep running there, so this is
    reversible by connecting it again (which issues a new pairing token). History is kept: the audit
    log, plans, test runs and anything the fleet produced stay exactly where they are.
    """
    inst = store.get_instance(instance_id)
    if not inst:
        raise HTTPException(404, "no such instance")
    applied = store.applied_for(instance_id)
    removed = store.delete_instance(instance_id)
    detail = f"{inst.get('environment')}, {inst.get('mode')}"
    if applied:
        detail += f"; had {applied['name']} v{applied['version']} applied"
    store.record(user["email"], "instance.removed", instance_id, detail)
    return {"ok": True, "id": instance_id, "removed": removed, "had_applied": applied}


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
    # where each MCP server is configured on the instance: every profile of the last import (a revert plan's
    # ``live`` holds only the blueprint's), and how it signs in from the last discovery
    probe = store.integrations().get(inst["id"]) or {}
    auth = {s["name"]: s.get("auth") for servers in (probe.get("servers") or {}).values() for s in servers if s.get("name")}
    sources = mcp_sources({**(store.live_state_for(inst["id"]) or {}), **live}, auth)
    plan = compute_plan(bp, live, target_instance=inst["id"], agent_installed=(inst["mode"] == "agent" and inst.get("agent_version") is not None), environment=inst["environment"],
                        default_approvals=approval_floor(_settings()), sources=sources)
    plan["id"] = "plan_" + uuid.uuid4().hex[:10]
    plan["status"] = "planned"
    plan["approvals"] = []
    plan["created_by"] = who
    plan["created_at"] = time.time()
    store.save_plan(plan)
    store.record(who, "plan.created", f"{bp.metadata.name} v{bp.metadata.version} → {inst['id']}", f"{len(plan['changes'])} changes{why}")
    if plan["environment"] == "production" and plan.get("approvals_required"):
        _notify_people("plan.approval", key=f"plan:{plan['id']}:approval",
                       ctx={"instance": inst["id"], "link": f"/plans/{plan['id']}",
                            "detail": f"{bp.metadata.name} v{bp.metadata.version}, {len(plan['changes'])} changes, "
                                      f"{plan['approvals_required']} approval(s) needed; created by {who}."},
                       people=_people_with("plans.approve.production", except_=[who]))
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
    pre = _preflight(plan)
    if not pre["satisfied"]:
        raise HTTPException(409, f"the blueprint's tests must pass on lab or staging first ({pre['passed']} of {pre['total']} passed); "
                                 "run them in Test Lab")
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
    _wf_escalate()  # heartbeats are Fleet Control's clock: an overdue workflow gate escalates within a beat
    _wf_kanban_sync(instance_id)  # and Kanban-run workflows are followed at the same pace
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


class AgentOutput(OutputCreate):
    produced_by: str = Field(..., min_length=1, max_length=120, description="The agent (profile) that produced it.")


@app.post("/agent/v1/instances/{instance_id}/outputs", status_code=201)
def agent_output(instance_id: str, body: AgentOutput, inst: str = Depends(agent_instance)) -> dict:
    """An instance's agent filing what one of its profiles produced, into a content zone."""
    if inst != instance_id:
        raise HTTPException(403)
    source = body.source or {"kind": "agent"}
    return _save_output(body, produced_by=body.produced_by, source=source, instance_id=instance_id,
                        actor="agent:" + instance_id, action="output.produced")


@app.post("/agent/v1/instances/{instance_id}/rooms", status_code=201)
def agent_open_room(instance_id: str, body: RoomCreate, agent_profile: Optional[str] = Header(default=None, alias="X-Hermes-Profile"),
                    inst: str = Depends(agent_instance)) -> dict:
    """An orchestrator opening a Decision Room for a case it is working."""
    if inst != instance_id:
        raise HTTPException(403)
    who = (agent_profile or "agent").strip()[:120]
    room = _open_room(body, opened_by=who, kind="agent", actor="agent:" + instance_id, instance_id=instance_id)
    return {"id": room["id"], "status": room["status"], "zone": room["zone"]}


@app.post("/agent/v1/rooms/{room_id}/findings", status_code=201)
def agent_add_finding(room_id: str, body: FindingBody, inst: str = Depends(agent_instance)) -> dict:
    """What an agent found, filed into a room. Agents never decide: they add findings and evidence."""
    room = store.get_room(room_id)
    if not room:
        raise HTTPException(404, "no such decision room")
    check = None
    if body.tool:
        # checked against what this instance recorded, never taken on the agent's word
        run = store.get_test_run(body.run_id) if body.run_id else None
        run = run if run and run.get("instance_id") == inst else None
        events = store.session_tool_events(inst, body.session_id) if body.session_id and run is None else None
        check = check_tool(body.tool, run=run, events=events)
    try:
        finding = new_finding(agent=body.agent, text=body.text, verdict=body.verdict, run_id=body.run_id, at=time.time(),
                              basis=body.basis, tool=body.tool, session_id=body.session_id, tool_check=check)
    except RoomError as exc:
        raise HTTPException(422, str(exc))
    store.update_room(room_id, lambda r: (r["findings"].append(finding), r.update(updated_at=finding["at"]))[0])
    store.record("agent:" + inst, "room.finding_added", room_id, f"{finding['agent']}: {finding['text'][:100]}")
    return {"ok": True, "findings": len(room["findings"]) + 1}


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
    if job["kind"] == "hermes_run" and job["meta"].get("architect_session"):
        _architect_result(job)
    if job["kind"] == "hermes_run" and job["meta"].get("ask_thread"):
        _ask_result(job)
    if job["kind"] == "hermes_run" and job["meta"].get("workflow_run"):
        _wf_result(job)
    if job["kind"] == "kanban_submit" and job["meta"].get("workflow_run"):
        _wf_kanban_submitted(job)
    if job["kind"] == "kanban_read" and job["meta"].get("workflow_run"):
        _wf_kanban_read(job)
    if job["kind"] == "run_test" and job["meta"].get("test_run"):
        _test_result(job)
    if job["kind"] in ("mcp_discover", "mcp_write") and job["meta"].get("discovery"):
        _discovery_result(job, inst)
    if job["kind"] == "deliver_message" and job["meta"].get("delivery"):
        _delivery_result(job)
    if job["kind"] in ("messaging_discover", "channel_route", "webhooks_enable"):
        _messaging_result(job, inst)
    if job["kind"] == "drift_scan" and job["status"] == "done":
        report = store.drift_report(inst) or {}
        if report.get("drift"):
            fields = sum(len(v) for v in report["drift"].values())
            ctx = {"instance": inst, "link": f"/instances/{inst}/drift",
                   "detail": f"{fields} field(s) on {len(report['drift'])} profile(s) differ from "
                             f"{report.get('blueprint')} v{report.get('version')}."}
            _notify("drift.detected", key=f"drift:{inst}:{job['id']}", ctx=ctx)
            _notify_people("drift.detected", key=f"drift:{inst}:{job['id']}", ctx=ctx, people=_people_with("drift.read"))
    if job["kind"] == "apply" and job["meta"].get("plan_id"):
        plan_id = job["meta"]["plan_id"]
        plan = store.get_plan(plan_id) or {}
        bp_ref = plan.get("blueprint") or {}
        ctx = {"instance": inst, "link": f"/plans/{plan_id}",
               "detail": f"{bp_ref.get('name')} v{bp_ref.get('version')}"
                         + ("" if job["status"] == "done" else f": {((job.get('result') or {}).get('error') or 'see the plan')[:200]}")}
        _notify("apply.completed" if job["status"] == "done" else "apply.failed", key=f"plan:{plan_id}:{job['status']}", ctx=ctx)
        if job["status"] != "done":
            _notify_people("apply.failed", key=f"plan:{plan_id}:failed", ctx=ctx, people=_people_with("plans.apply.nonprod"))
    applied = store.applied_for(inst) if job["kind"] == "apply" and job["status"] == "done" else None
    if applied:
        # drift detection starts right after an apply, against the version just applied
        bp = _blueprint(applied["name"], applied["version"])
        scan = store.enqueue_job(inst, "drift_scan", {"managed": bp.managed_fields()}, {"blueprint": applied["name"], "version": applied["version"]})
        store.record("fleetcontrol", "drift.scan_requested", inst, f"{applied['name']} v{applied['version']} after apply ({scan['id']})")
        # and a fresh import, so a profile the apply created shows up wherever profiles are chosen
        imp = store.enqueue_job(inst, "import_profiles", {}, {"after_apply": job["id"]})
        store.record("fleetcontrol", "instance.import_requested", inst, f"after apply ({imp['id']})")
    return {"ok": True}
