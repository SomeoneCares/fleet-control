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
from .rooms import RoomError, decide, new_evidence, new_finding, new_room, room_row, room_view
from .planner import compute_plan, to_agent_job
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
            "workspace_name": _settings()["workspace_name"]}


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
    return _output_out(doc)


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


@app.post("/api/v1/rooms/{room_id}/evidence", status_code=201)
def add_evidence(room_id: str, body: EvidenceAdd, user: dict = Depends(require("rooms.open"))) -> dict:
    room = _room(room_id, user)
    if body.kind == "file" and body.ref and (store.get_file(body.ref) or {}).get("zone") not in _my_zones(user):
        raise HTTPException(404, "no such file in a zone you may read")
    if body.kind == "output" and body.ref and (store.get_output(body.ref) or {}).get("zone") not in _my_zones(user):
        raise HTTPException(404, "no such output in a zone you may read")
    try:
        item = new_evidence(kind=body.kind, label=body.label, ref=body.ref, source=body.source, verdict=body.verdict,
                            added_by=user["email"], at=time.time())
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


# The two agent routes for rooms live with the rest of the /agent/v1 family, below `agent_instance`.


# ----------------------------------------------------------------------------- Integrations (Slice 3)


@app.get("/api/v1/integrations")
def list_integrations(user: dict = Depends(require("instances.read"))) -> dict:
    """MCP servers and model providers across the estate, with health, tools and which agents may use them."""
    instances = store.list_instances()
    live = {i["id"]: store.live_state_for(i["id"]) or {} for i in instances}
    probes = store.integrations()
    # who uses a server is what is applied, not the newest draft; a newer draft only shows as "planned"
    applied, drafts = [], []
    for versions in store.all_blueprints().values():
        done = [v for v, rec in versions.items() if rec.get("status") == "applied"]
        newest_applied = max(done) if done else None
        if newest_applied is not None:
            applied.append(versions[newest_applied]["parsed"])
        newest = max(versions)
        if newest_applied is None or newest > newest_applied:
            drafts.append(versions[newest]["parsed"])
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
    plan = compute_plan(bp, live, target_instance=inst["id"], agent_installed=(inst["mode"] == "agent" and inst.get("agent_version") is not None), environment=inst["environment"],
                        default_approvals=approval_floor(_settings()))
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
    try:
        finding = new_finding(agent=body.agent, text=body.text, verdict=body.verdict, run_id=body.run_id, at=time.time())
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
    if job["kind"] == "run_test" and job["meta"].get("test_run"):
        _test_result(job)
    if job["kind"] in ("mcp_discover", "mcp_write") and job["meta"].get("discovery"):
        _discovery_result(job, inst)
    applied = store.applied_for(inst) if job["kind"] == "apply" and job["status"] == "done" else None
    if applied:
        # drift detection starts right after an apply, against the version just applied
        bp = _blueprint(applied["name"], applied["version"])
        scan = store.enqueue_job(inst, "drift_scan", {"managed": bp.managed_fields()}, {"blueprint": applied["name"], "version": applied["version"]})
        store.record("fleetcontrol", "drift.scan_requested", inst, f"{applied['name']} v{applied['version']} after apply ({scan['id']})")
    return {"ok": True}
