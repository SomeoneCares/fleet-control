"""In-memory store for the Slice 1 scaffold. Swap for PostgreSQL (SQLAlchemy) before staging use.

Kept deliberately dumb: dictionaries guarded by one lock, plus an append-only audit list.
The shapes here are the ones the database tables will have (build document §4.1).
"""

from __future__ import annotations

import queue
import secrets
import threading
import time
import uuid
from typing import Any, Optional


class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.instances: dict[str, dict] = {}
        self.pairing_tokens: dict[str, str] = {}  # token -> instance_id (single use)
        self.agent_tokens: dict[str, str] = {}  # token -> instance_id
        self.live_state: dict[str, dict[str, dict]] = {}  # instance_id -> {profile: state}
        self.blueprints: dict[str, dict[int, dict]] = {}  # name -> version -> {yaml, parsed, status}
        self.plans: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.job_queues: dict[str, "queue.Queue[dict]"] = {}
        self.events: list[dict] = []
        self.audit: list[dict] = []
        self.drift: dict[str, dict] = {}  # instance_id -> latest report {at, blueprint, version, drift, excepted, ...}
        self.drift_exceptions: dict[str, list[dict]] = {}  # instance_id -> [{profile, field, expires_at, by, reason}]

    # ---- audit ---------------------------------------------------------------
    def record(self, actor: str, action: str, target: str, detail: str = "") -> None:
        with self.lock:
            self.audit.append({"id": uuid.uuid4().hex, "ts": time.time(), "actor": actor, "action": action, "target": target, "detail": detail})

    # ---- instances -----------------------------------------------------------
    def create_instance(self, instance_id: str, environment: str, owner: str, mode: str) -> dict:
        with self.lock:
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
            self.instances[instance_id] = inst
            self.job_queues[instance_id] = queue.Queue()
            token = "pair_" + secrets.token_urlsafe(24)
            self.pairing_tokens[token] = instance_id
            inst["pairing_token"] = token
            return inst

    def pair(self, pairing_token: str, agent_version: str, report: dict) -> Optional[tuple[str, str]]:
        with self.lock:
            instance_id = self.pairing_tokens.pop(pairing_token, None)
            if not instance_id:
                return None
            token = "agt_" + secrets.token_urlsafe(32)
            self.agent_tokens[token] = instance_id
            self._absorb_report(instance_id, agent_version, report)
            self.instances[instance_id].pop("pairing_token", None)
            return instance_id, token

    def instance_for_agent_token(self, token: str) -> Optional[str]:
        return self.agent_tokens.get(token)

    def _absorb_report(self, instance_id: str, agent_version: Optional[str], report: dict) -> None:
        inst = self.instances[instance_id]
        inst["agent_version"] = agent_version or inst.get("agent_version")
        inst["hermes_version"] = report.get("hermes_version") or inst.get("hermes_version")
        inst["capabilities"] = report.get("capabilities") or inst.get("capabilities") or []
        inst["report"] = report or inst.get("report") or {}
        inst["last_heartbeat"] = time.time()
        inst["status"] = "healthy" if report.get("surfaces", {}).get("api") == "ok" else "degraded"

    def heartbeat(self, instance_id: str, agent_version: str, report: dict) -> None:
        with self.lock:
            self._absorb_report(instance_id, agent_version, report)

    # ---- jobs ----------------------------------------------------------------
    def enqueue_job(self, instance_id: str, kind: str, params: dict, meta: Optional[dict] = None) -> dict:
        with self.lock:
            job = {"id": "job_" + uuid.uuid4().hex[:12], "instance_id": instance_id, "kind": kind, "params": params, "meta": meta or {}, "status": "queued", "result": None, "created_at": time.time()}
            self.jobs[job["id"]] = job
            self.job_queues[instance_id].put(job)
            return job

    def next_job(self, instance_id: str, timeout: float = 25.0) -> Optional[dict]:
        q = self.job_queues.get(instance_id)
        if q is None:
            return None
        try:
            job = q.get(timeout=timeout)
        except queue.Empty:
            return None
        with self.lock:
            job["status"] = "running"
        return {"id": job["id"], "kind": job["kind"], "params": job["params"]}

    def complete_job(self, job_id: str, result: dict, *, instance_id: str) -> Optional[dict]:
        """Record a job result reported by the agent of ``instance_id``.

        Ownership is checked before anything is written: a job queued for another instance is
        left untouched and None is returned, so one agent can never overwrite another
        instance's live state or flip the status of its plans."""
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job["instance_id"] != instance_id:
                return None
            job["status"] = "done" if result.get("ok") else "failed"
            job["result"] = result
            job["finished_at"] = time.time()
            # side effects by kind
            if job["kind"] in ("import_profiles",) and result.get("ok"):
                self.live_state[job["instance_id"]] = result.get("profiles", {})
            if job["kind"] == "drift_scan" and result.get("ok"):
                drift, excepted = self._apply_exceptions(job["instance_id"], result.get("drift", {}))
                self.drift[job["instance_id"]] = {
                    "at": time.time(), "blueprint": job["meta"].get("blueprint"), "version": job["meta"].get("version"),
                    "drift": drift, "excepted": excepted,
                }
            if job["kind"] == "apply":
                plan_id = job["meta"].get("plan_id")
                if plan_id and plan_id in self.plans:
                    self.plans[plan_id]["status"] = "applied" if result.get("ok") else "failed"
                    self.plans[plan_id]["apply_result"] = result
            return job

    # ---- drift ---------------------------------------------------------------
    def active_exceptions(self, instance_id: str, now: Optional[float] = None) -> list[dict]:
        now = time.time() if now is None else now
        return [e for e in self.drift_exceptions.get(instance_id, []) if e["expires_at"] > now]

    def _apply_exceptions(self, instance_id: str, drift: dict) -> tuple[dict, dict]:
        """Split a scan's drift into (reported, excepted) using the instance's unexpired exceptions."""
        active = {(e["profile"], e["field"]) for e in self.active_exceptions(instance_id)}
        reported: dict[str, list] = {}
        excepted: dict[str, list] = {}
        for profile, diffs in drift.items():
            for d in diffs:
                (excepted if (profile, d["field"]) in active else reported).setdefault(profile, []).append(d)
        return reported, excepted

    def move_drift(self, instance_id: str, chosen: dict, bucket: str) -> None:
        """Take resolved fields out of the open drift report and file them under ``bucket``."""
        with self.lock:
            report = self.drift.get(instance_id)
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

    def add_exceptions(self, instance_id: str, chosen: dict, expires_at: float, by: str, reason: Optional[str] = None) -> None:
        with self.lock:
            entries = self.drift_exceptions.setdefault(instance_id, [])
            for profile, diffs in chosen.items():
                for d in diffs:
                    entries.append({"profile": profile, "field": d["field"], "expires_at": expires_at, "by": by,
                                    "reason": reason, "created_at": time.time()})
            self.move_drift(instance_id, chosen, "excepted")

    # ---- blueprints ----------------------------------------------------------
    def save_blueprint(self, name: str, version: int, yaml_text: str, parsed: dict, author: str) -> dict:
        with self.lock:
            rec = {"name": name, "version": version, "yaml": yaml_text, "parsed": parsed, "status": "draft", "author": author, "created_at": time.time()}
            self.blueprints.setdefault(name, {})[version] = rec
            return rec
