"""Fleet Control store: SQLAlchemy Core over PostgreSQL (deployments) or SQLite (development, tests).

Each table keeps the columns queries filter on plus a JSON document (JSONB on PostgreSQL) with the
rest, so the records the API returns keep the shapes of the Slice 1 scaffold (build document §4.1).
Blueprints are YAML text plus the parsed JSON; the audit log is insert-only. Pairing and agent tokens
are kept as SHA-256 hashes, like sessions. Every write goes through a method here: callers get copies
and never change stored state by mutating them.

Database: FLEETCONTROL_DATABASE_URL, e.g. ``postgresql+psycopg://fc:<password>@db:5432/fleetcontrol`` or
``sqlite:///path/fleetcontrol.db``. Unset means an in-memory SQLite database that ends with the process
(tests). Tables are created on start; migrations (Alembic) come with the first schema change after v1.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from sqlalchemy import (
    JSON, Boolean, Column, Float, Integer, MetaData, String, Table, Text, create_engine, delete, func, insert, select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.pool import StaticPool

Doc = JSON().with_variant(JSONB(), "postgresql")
META = MetaData()

USERS = Table(
    "users", META,
    Column("email", String(320), primary_key=True),
    Column("name", Text, nullable=False),
    Column("role", String(32), nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("disabled", Boolean, nullable=False, default=False),
    Column("created_at", Float, nullable=False),
    Column("last_login", Float),
)
SESSIONS = Table(
    "sessions", META,
    Column("key", String(64), primary_key=True),  # sha256 of the cookie value
    Column("email", String(320), nullable=False, index=True),
    Column("expires", Float, nullable=False),
    Column("seconds", Integer, nullable=False),
)
LOGIN_FAILURES = Table(
    "login_failures", META,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", String(320), nullable=False, index=True),
    Column("at", Float, nullable=False),
)
INSTANCES = Table(
    "instances", META,
    Column("id", String(64), primary_key=True),
    Column("created_at", Float, nullable=False),
    Column("doc", Doc, nullable=False),
)
TOKENS = Table(
    "tokens", META,
    Column("key", String(64), primary_key=True),  # sha256 of the token
    Column("kind", String(16), nullable=False),  # pairing (single use) | agent
    Column("instance_id", String(64), nullable=False, index=True),
    Column("created_at", Float, nullable=False),
)
LIVE_STATE = Table(
    "live_state", META,
    Column("instance_id", String(64), primary_key=True),
    Column("at", Float, nullable=False),
    Column("profiles", Doc, nullable=False),
)
BLUEPRINTS = Table(
    "blueprints", META,
    Column("name", String(64), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("status", String(16), nullable=False),  # draft | applied (immutable)
    Column("yaml", Text, nullable=False),
    Column("parsed", Doc, nullable=False),
    Column("author", String(320), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float),
    Column("updated_by", String(320)),
)
PLANS = Table(
    "plans", META,
    Column("id", String(32), primary_key=True),
    Column("status", String(16), nullable=False, index=True),
    Column("target_instance", String(64), nullable=False, index=True),
    Column("created_at", Float, nullable=False),
    Column("doc", Doc, nullable=False),
)
JOBS = Table(
    "jobs", META,
    Column("seq", Integer, primary_key=True, autoincrement=True),  # delivery order
    Column("id", String(32), nullable=False, unique=True),
    Column("instance_id", String(64), nullable=False, index=True),
    Column("status", String(16), nullable=False, index=True),
    Column("created_at", Float, nullable=False),
    Column("doc", Doc, nullable=False),
)
EVENTS = Table(
    "events", META,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("instance_id", String(64), nullable=False, index=True),
    Column("kind", String(64), index=True),
    Column("doc", Doc, nullable=False),
)
AUDIT = Table(  # insert-only: there is no method that updates or deletes a row
    "audit", META,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("id", String(32), nullable=False, unique=True),
    Column("ts", Float, nullable=False),
    Column("actor", String(320), nullable=False),
    Column("action", String(64), nullable=False),
    Column("target", Text, nullable=False),
    Column("detail", Text, nullable=False),
)
DRIFT = Table(
    "drift_reports", META,
    Column("instance_id", String(64), primary_key=True),
    Column("report", Doc, nullable=False),  # {at, blueprint, version, drift, excepted, ignored, accepted}
)
DRIFT_EXCEPTIONS = Table(
    "drift_exceptions", META,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("instance_id", String(64), nullable=False, index=True),
    Column("profile", Text, nullable=False),
    Column("field", String(32), nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("by", String(320), nullable=False),
    Column("reason", Text),
    Column("created_at", Float, nullable=False),
)
APPLIED = Table(  # the last successful apply per instance
    "applied", META,
    Column("instance_id", String(64), primary_key=True),
    Column("name", String(64), nullable=False),
    Column("version", Integer, nullable=False),
    Column("plan_id", String(32), nullable=False),
    Column("at", Float, nullable=False),
)

USER_FIELDS = frozenset({"name", "role", "password_hash", "disabled", "last_login"})
_EXCEPTION_FIELDS = ("profile", "field", "expires_at", "by", "reason", "created_at")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _one(c: Connection, query) -> Optional[dict]:
    row = c.execute(query).mappings().first()
    return dict(row) if row else None


def _all(c: Connection, query) -> list[dict]:
    return [dict(r) for r in c.execute(query).mappings()]


def _upsert(c: Connection, table: Table, key: dict, values: dict) -> None:
    """Update the row with ``key``, or insert it (portable across SQLite and PostgreSQL)."""
    cond = [table.c[k] == v for k, v in key.items()]
    if c.execute(update(table).where(*cond).values(**values)).rowcount == 0:
        c.execute(insert(table).values(**key, **values))


class Store:
    def __init__(self, url: Optional[str] = None):
        url = os.environ.get("FLEETCONTROL_DATABASE_URL", "") if url is None else url
        if url in ("", "sqlite://", "sqlite:///:memory:"):
            # one shared connection, so every thread sees the same in-memory database
            self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        elif url.startswith("sqlite"):
            self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
        else:
            self.engine = create_engine(url, pool_pre_ping=True)
        META.create_all(self.engine)
        # Serialises this process's transactions; SQLite allows one writer anyway. Cross-process
        # safety on PostgreSQL comes from conditional updates (job claims, plan transitions).
        self.lock = threading.RLock()
        self._job_ready = threading.Condition(self.lock)

    @property
    def url(self) -> str:
        return self.engine.url.render_as_string(hide_password=True)

    @contextmanager
    def _tx(self) -> Iterator[Connection]:
        with self.lock, self.engine.begin() as c:
            yield c

    # ---- audit ---------------------------------------------------------------
    def record(self, actor: str, action: str, target: str, detail: str = "") -> None:
        with self._tx() as c:
            c.execute(insert(AUDIT).values(id=uuid.uuid4().hex, ts=time.time(), actor=actor, action=action,
                                           target=target, detail=detail))

    def audit_log(self, limit: Optional[int] = None, *, newest_first: bool = True) -> list[dict]:
        q = select(AUDIT.c.id, AUDIT.c.ts, AUDIT.c.actor, AUDIT.c.action, AUDIT.c.target, AUDIT.c.detail)
        q = q.order_by(AUDIT.c.seq.desc() if newest_first else AUDIT.c.seq)
        if limit:
            q = q.limit(limit)
        with self._tx() as c:
            return _all(c, q)

    # ---- people and sessions -------------------------------------------------
    def has_users(self) -> bool:
        with self._tx() as c:
            return c.execute(select(func.count()).select_from(USERS)).scalar_one() > 0

    def add_user(self, email: str, name: str, role: str, password_hash: str) -> dict:
        user = {"email": email, "name": name, "role": role, "password_hash": password_hash,
                "disabled": False, "created_at": time.time(), "last_login": None}
        with self._tx() as c:
            c.execute(insert(USERS).values(**user))
        return user

    def get_user(self, email: str) -> Optional[dict]:
        with self._tx() as c:
            return _one(c, select(USERS).where(USERS.c.email == email))

    def list_users(self) -> list[dict]:
        with self._tx() as c:
            return _all(c, select(USERS).order_by(USERS.c.email))

    def update_user(self, email: str, /, **fields: Any) -> Optional[dict]:
        unknown = set(fields) - USER_FIELDS
        if unknown:
            raise ValueError(f"not a user field: {', '.join(sorted(unknown))}")
        with self._tx() as c:
            if fields:
                c.execute(update(USERS).where(USERS.c.email == email).values(**fields))
            return _one(c, select(USERS).where(USERS.c.email == email))

    def create_session(self, email: str, token: str, key: str, seconds: int) -> None:
        """``key`` is sha256(token); the token itself is never stored."""
        with self._tx() as c:
            c.execute(insert(SESSIONS).values(key=key, email=email, expires=time.time() + seconds, seconds=seconds))

    def session_user(self, key: str) -> Optional[dict]:
        """The signed-in user for a session, sliding its expiry; None when expired, unknown or disabled."""
        with self._tx() as c:
            s = _one(c, select(SESSIONS).where(SESSIONS.c.key == key))
            if not s:
                return None
            user = _one(c, select(USERS).where(USERS.c.email == s["email"]))
            if s["expires"] < time.time() or not user or user["disabled"]:
                c.execute(delete(SESSIONS).where(SESSIONS.c.key == key))
                return None
            c.execute(update(SESSIONS).where(SESSIONS.c.key == key).values(expires=time.time() + s["seconds"]))
            return user

    def drop_session(self, key: str) -> None:
        with self._tx() as c:
            c.execute(delete(SESSIONS).where(SESSIONS.c.key == key))

    def drop_sessions_for(self, email: str) -> None:
        with self._tx() as c:
            c.execute(delete(SESSIONS).where(SESSIONS.c.email == email))

    def recent_failures(self, email: str, window: float) -> int:
        with self._tx() as c:
            c.execute(delete(LOGIN_FAILURES).where(LOGIN_FAILURES.c.email == email,
                                                   LOGIN_FAILURES.c.at <= time.time() - window))
            return c.execute(select(func.count()).select_from(LOGIN_FAILURES)
                             .where(LOGIN_FAILURES.c.email == email)).scalar_one()

    def login_failed(self, email: str) -> None:
        with self._tx() as c:
            c.execute(insert(LOGIN_FAILURES).values(email=email, at=time.time()))

    def clear_failures(self, email: str) -> None:
        with self._tx() as c:
            c.execute(delete(LOGIN_FAILURES).where(LOGIN_FAILURES.c.email == email))

    # ---- instances -----------------------------------------------------------
    def create_instance(self, instance_id: str, environment: str, owner: str, mode: str) -> dict:
        """The new instance plus its single-use pairing token (returned once; only its hash is kept)."""
        inst = {
            "id": instance_id,
            "environment": environment,
            "owner": owner,
            "mode": mode,  # agent | api-only
            "status": "pending",
            "hermes_version": None,
            "agent_version": None,
            "capabilities": [],
            "report": {},
            "last_heartbeat": None,
            "created_at": time.time(),
        }
        token = "pair_" + secrets.token_urlsafe(24)
        with self._tx() as c:
            c.execute(insert(INSTANCES).values(id=instance_id, created_at=inst["created_at"], doc=inst))
            c.execute(insert(TOKENS).values(key=_hash(token), kind="pairing", instance_id=instance_id, created_at=time.time()))
        return {**inst, "pairing_token": token}

    def get_instance(self, instance_id: str) -> Optional[dict]:
        with self._tx() as c:
            return self._instance(c, instance_id)

    def list_instances(self) -> list[dict]:
        with self._tx() as c:
            return [r["doc"] for r in _all(c, select(INSTANCES.c.doc).order_by(INSTANCES.c.created_at))]

    def pair(self, pairing_token: str, agent_version: str, report: dict) -> Optional[tuple[str, str]]:
        with self._tx() as c:
            row = _one(c, select(TOKENS).where(TOKENS.c.key == _hash(pairing_token), TOKENS.c.kind == "pairing"))
            if not row or c.execute(delete(TOKENS).where(TOKENS.c.key == row["key"])).rowcount != 1:
                return None
            token = "agt_" + secrets.token_urlsafe(32)
            c.execute(insert(TOKENS).values(key=_hash(token), kind="agent", instance_id=row["instance_id"], created_at=time.time()))
            self._absorb_report(c, row["instance_id"], agent_version, report)
            return row["instance_id"], token

    def instance_for_agent_token(self, token: str) -> Optional[str]:
        with self._tx() as c:
            row = _one(c, select(TOKENS.c.instance_id).where(TOKENS.c.key == _hash(token), TOKENS.c.kind == "agent"))
        return row["instance_id"] if row else None

    def heartbeat(self, instance_id: str, agent_version: str, report: dict) -> None:
        with self._tx() as c:
            self._absorb_report(c, instance_id, agent_version, report)

    def _instance(self, c: Connection, instance_id: str) -> Optional[dict]:
        row = _one(c, select(INSTANCES.c.doc).where(INSTANCES.c.id == instance_id))
        return row["doc"] if row else None

    def _absorb_report(self, c: Connection, instance_id: str, agent_version: Optional[str], report: dict) -> None:
        inst = self._instance(c, instance_id)
        if inst is None:
            return
        inst["agent_version"] = agent_version or inst.get("agent_version")
        inst["hermes_version"] = report.get("hermes_version") or inst.get("hermes_version")
        inst["capabilities"] = report.get("capabilities") or inst.get("capabilities") or []
        inst["report"] = report or inst.get("report") or {}
        inst["last_heartbeat"] = time.time()
        inst["status"] = "healthy" if report.get("surfaces", {}).get("api") == "ok" else "degraded"
        c.execute(update(INSTANCES).where(INSTANCES.c.id == instance_id).values(doc=inst))

    def live_state_for(self, instance_id: str) -> Optional[dict[str, dict]]:
        """{profile: state} from the instance's last successful import, or None before the first one."""
        with self._tx() as c:
            row = _one(c, select(LIVE_STATE.c.profiles).where(LIVE_STATE.c.instance_id == instance_id))
        return row["profiles"] if row else None

    # ---- jobs ----------------------------------------------------------------
    def enqueue_job(self, instance_id: str, kind: str, params: dict, meta: Optional[dict] = None) -> dict:
        job = {"id": "job_" + uuid.uuid4().hex[:12], "instance_id": instance_id, "kind": kind, "params": params,
               "meta": meta or {}, "status": "queued", "result": None, "created_at": time.time()}
        with self._tx() as c:
            c.execute(insert(JOBS).values(id=job["id"], instance_id=instance_id, status="queued",
                                          created_at=job["created_at"], doc=job))
        with self._job_ready:
            self._job_ready.notify_all()
        return job

    def get_job(self, job_id: str) -> Optional[dict]:
        with self._tx() as c:
            row = _one(c, select(JOBS.c.doc).where(JOBS.c.id == job_id))
        return row["doc"] if row else None

    def next_job(self, instance_id: str, timeout: float = 25.0) -> Optional[dict]:
        """Long-poll: the instance's oldest queued job, claimed for delivery, or None after ``timeout``.

        Queued jobs survive a restart. The claim is a conditional update, so two API processes on
        one PostgreSQL never hand the same job out twice; waiters also re-check every second, so a
        job queued by another process is picked up without an in-process wake-up."""
        if self.get_instance(instance_id) is None:
            return None
        deadline = time.monotonic() + timeout
        with self._job_ready:
            while True:
                with self._tx() as c:
                    for row in _all(c, select(JOBS.c.doc).where(JOBS.c.instance_id == instance_id,
                                                               JOBS.c.status == "queued").order_by(JOBS.c.seq).limit(5)):
                        job = row["doc"]
                        job["status"] = "running"
                        claimed = c.execute(update(JOBS).where(JOBS.c.id == job["id"], JOBS.c.status == "queued")
                                            .values(status="running", doc=job)).rowcount
                        if claimed == 1:
                            return {"id": job["id"], "kind": job["kind"], "params": job["params"]}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._job_ready.wait(min(remaining, 1.0))

    def complete_job(self, job_id: str, result: dict, *, instance_id: str) -> Optional[dict]:
        """Record a job result reported by the agent of ``instance_id``.

        Ownership is checked before anything is written: a job queued for another instance is
        left untouched and None is returned, so one agent can never overwrite another
        instance's live state or flip the status of its plans."""
        with self._tx() as c:
            row = _one(c, select(JOBS.c.doc).where(JOBS.c.id == job_id))
            job = row["doc"] if row else None
            if not job or job["instance_id"] != instance_id:
                return None
            ok = bool(result.get("ok"))
            now = time.time()
            job.update(status="done" if ok else "failed", result=result, finished_at=now)
            c.execute(update(JOBS).where(JOBS.c.id == job_id).values(status=job["status"], doc=job))
            # side effects by kind
            if job["kind"] == "import_profiles" and ok:
                _upsert(c, LIVE_STATE, {"instance_id": instance_id}, {"at": now, "profiles": result.get("profiles", {})})
            if job["kind"] == "drift_scan" and ok:
                drift, excepted = self._apply_exceptions(c, instance_id, result.get("drift", {}))
                report = {"at": now, "blueprint": job["meta"].get("blueprint"), "version": job["meta"].get("version"),
                          "drift": drift, "excepted": excepted}
                _upsert(c, DRIFT, {"instance_id": instance_id}, {"report": report})
            if job["kind"] == "apply":
                plan_id = job["meta"].get("plan_id")
                plan = self._plan(c, plan_id) if plan_id else None
                if plan:
                    plan["status"] = "applied" if ok else "failed"
                    plan["apply_result"] = result
                    self._put_plan(c, plan)
                    if ok:
                        bp = plan["blueprint"]
                        _upsert(c, APPLIED, {"instance_id": instance_id},
                                {"name": bp["name"], "version": bp["version"], "plan_id": plan_id, "at": now})
                        c.execute(update(BLUEPRINTS).where(BLUEPRINTS.c.name == bp["name"], BLUEPRINTS.c.version == bp["version"])
                                  .values(status="applied"))  # immutable from here on
            return job

    # ---- drift ---------------------------------------------------------------
    def drift_report(self, instance_id: str) -> Optional[dict]:
        with self._tx() as c:
            return self._drift(c, instance_id)

    def active_exceptions(self, instance_id: str, now: Optional[float] = None) -> list[dict]:
        with self._tx() as c:
            return self._active_exceptions(c, instance_id, time.time() if now is None else now)

    def move_drift(self, instance_id: str, chosen: dict, bucket: str) -> None:
        """Take resolved fields out of the open drift report and file them under ``bucket``."""
        with self._tx() as c:
            self._move_drift(c, instance_id, chosen, bucket)

    def add_exceptions(self, instance_id: str, chosen: dict, expires_at: float, by: str, reason: Optional[str] = None) -> None:
        with self._tx() as c:
            for profile, diffs in chosen.items():
                for d in diffs:
                    c.execute(insert(DRIFT_EXCEPTIONS).values(instance_id=instance_id, profile=profile, field=d["field"],
                                                              expires_at=expires_at, by=by, reason=reason, created_at=time.time()))
            self._move_drift(c, instance_id, chosen, "excepted")

    def _drift(self, c: Connection, instance_id: str) -> Optional[dict]:
        row = _one(c, select(DRIFT.c.report).where(DRIFT.c.instance_id == instance_id))
        return row["report"] if row else None

    def _active_exceptions(self, c: Connection, instance_id: str, now: float) -> list[dict]:
        rows = _all(c, select(DRIFT_EXCEPTIONS).where(DRIFT_EXCEPTIONS.c.instance_id == instance_id,
                                                      DRIFT_EXCEPTIONS.c.expires_at > now).order_by(DRIFT_EXCEPTIONS.c.id))
        return [{k: r[k] for k in _EXCEPTION_FIELDS} for r in rows]

    def _apply_exceptions(self, c: Connection, instance_id: str, drift: dict) -> tuple[dict, dict]:
        """Split a scan's drift into (reported, excepted) using the instance's unexpired exceptions."""
        active = {(e["profile"], e["field"]) for e in self._active_exceptions(c, instance_id, time.time())}
        reported: dict[str, list] = {}
        excepted: dict[str, list] = {}
        for profile, diffs in drift.items():
            for d in diffs:
                (excepted if (profile, d["field"]) in active else reported).setdefault(profile, []).append(d)
        return reported, excepted

    def _move_drift(self, c: Connection, instance_id: str, chosen: dict, bucket: str) -> None:
        report = self._drift(c, instance_id)
        if not report:
            return
        filed = report.setdefault(bucket, {})
        for profile, diffs in chosen.items():
            fields = {d["field"] for d in diffs}
            left = [d for d in report["drift"].get(profile, []) if d["field"] not in fields]
            if left:
                report["drift"][profile] = left
            else:
                report["drift"].pop(profile, None)
            filed.setdefault(profile, []).extend(diffs)
        c.execute(update(DRIFT).where(DRIFT.c.instance_id == instance_id).values(report=report))

    # ---- blueprints ----------------------------------------------------------
    def get_blueprint(self, name: str, version: int) -> Optional[dict]:
        with self._tx() as c:
            return _one(c, select(BLUEPRINTS).where(BLUEPRINTS.c.name == name, BLUEPRINTS.c.version == version))

    def blueprint_versions(self, name: str) -> dict[int, dict]:
        """{version: record} for one blueprint; empty when the name is unknown."""
        with self._tx() as c:
            return {r["version"]: r for r in _all(c, select(BLUEPRINTS).where(BLUEPRINTS.c.name == name)
                                                  .order_by(BLUEPRINTS.c.version))}

    def all_blueprints(self) -> dict[str, dict[int, dict]]:
        out: dict[str, dict[int, dict]] = {}
        with self._tx() as c:
            for r in _all(c, select(BLUEPRINTS).order_by(BLUEPRINTS.c.name, BLUEPRINTS.c.version)):
                out.setdefault(r["name"], {})[r["version"]] = r
        return out

    def next_blueprint_version(self, name: str) -> int:
        with self._tx() as c:
            return (c.execute(select(func.max(BLUEPRINTS.c.version)).where(BLUEPRINTS.c.name == name)).scalar() or 0) + 1

    def save_blueprint(self, name: str, version: int, yaml_text: str, parsed: dict, author: str) -> dict:
        """A new draft (replacing a draft of the same version; callers refuse to overwrite applied ones)."""
        rec = {"name": name, "version": version, "yaml": yaml_text, "parsed": parsed, "status": "draft", "author": author,
               "created_at": time.time(), "updated_at": None, "updated_by": None}
        with self._tx() as c:
            _upsert(c, BLUEPRINTS, {"name": name, "version": version}, {k: v for k, v in rec.items() if k not in ("name", "version")})
        return rec

    def update_blueprint(self, name: str, version: int, yaml_text: str, parsed: dict, editor: str) -> dict:
        """Replace a draft's content in place; author and created_at stay as they were."""
        with self._tx() as c:
            c.execute(update(BLUEPRINTS).where(BLUEPRINTS.c.name == name, BLUEPRINTS.c.version == version)
                      .values(yaml=yaml_text, parsed=parsed, updated_at=time.time(), updated_by=editor))
            return _one(c, select(BLUEPRINTS).where(BLUEPRINTS.c.name == name, BLUEPRINTS.c.version == version))

    def set_blueprint_status(self, name: str, version: int, status: str) -> None:
        with self._tx() as c:
            c.execute(update(BLUEPRINTS).where(BLUEPRINTS.c.name == name, BLUEPRINTS.c.version == version).values(status=status))

    # ---- plans ---------------------------------------------------------------
    def save_plan(self, plan: dict) -> dict:
        with self._tx() as c:
            c.execute(insert(PLANS).values(id=plan["id"], status=plan["status"], target_instance=plan["target_instance"],
                                           created_at=plan["created_at"], doc=plan))
        return plan

    def get_plan(self, plan_id: str) -> Optional[dict]:
        with self._tx() as c:
            return self._plan(c, plan_id)

    def list_plans(self, status: Optional[str] = None) -> list[dict]:
        q = select(PLANS.c.doc).order_by(PLANS.c.created_at)
        if status:
            q = q.where(PLANS.c.status == status)
        with self._tx() as c:
            return [r["doc"] for r in _all(c, q)]

    def add_approval(self, plan_id: str, email: str) -> Optional[dict]:
        """Record one person's approval (once); returns the plan."""
        with self._tx() as c:
            plan = self._plan(c, plan_id)
            if plan and email not in plan["approvals"]:
                plan["approvals"].append(email)
                self._put_plan(c, plan)
            return plan

    def transition_plan(self, plan_id: str, from_status: str, to_status: str) -> bool:
        """Move a plan between statuses only if it is still in ``from_status`` (one of two concurrent applies wins)."""
        with self._tx() as c:
            if c.execute(update(PLANS).where(PLANS.c.id == plan_id, PLANS.c.status == from_status)
                         .values(status=to_status)).rowcount != 1:
                return False
            plan = self._plan(c, plan_id)
            plan["status"] = to_status
            self._put_plan(c, plan)
            return True

    def _plan(self, c: Connection, plan_id: str) -> Optional[dict]:
        row = _one(c, select(PLANS.c.doc).where(PLANS.c.id == plan_id))
        return row["doc"] if row else None

    def _put_plan(self, c: Connection, plan: dict) -> None:
        c.execute(update(PLANS).where(PLANS.c.id == plan["id"]).values(status=plan["status"], doc=plan))

    # ---- applied versions ----------------------------------------------------
    def applied_for(self, instance_id: str) -> Optional[dict]:
        with self._tx() as c:
            row = _one(c, select(APPLIED.c.name, APPLIED.c.version, APPLIED.c.plan_id, APPLIED.c.at)
                       .where(APPLIED.c.instance_id == instance_id))
        return row

    def all_applied(self) -> dict[str, dict]:
        with self._tx() as c:
            return {r.pop("instance_id"): r for r in _all(c, select(APPLIED))}

    # ---- agent events (evidence; Assurance consumes them in Slice 3) -----------
    def add_events(self, instance_id: str, events: list[dict]) -> int:
        with self._tx() as c:
            for e in events:
                kind = e.get("kind")
                c.execute(insert(EVENTS).values(instance_id=instance_id, kind=str(kind)[:64] if kind is not None else None,
                                                doc={**e, "instance_id": instance_id}))
        return len(events)

    def list_events(self, instance_id: Optional[str] = None, kind: Optional[str] = None, limit: int = 200) -> list[dict]:
        """The latest ``limit`` events, oldest first."""
        q = select(EVENTS.c.doc).order_by(EVENTS.c.seq.desc()).limit(limit)
        if instance_id:
            q = q.where(EVENTS.c.instance_id == instance_id)
        if kind:
            q = q.where(EVENTS.c.kind == kind)
        with self._tx() as c:
            return [r["doc"] for r in reversed(_all(c, q))]
