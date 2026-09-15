#!/usr/bin/env python3
"""Fill a local Fleet Control API with realistic demo data for the web client, with simulated agents.

    python scripts/dev_api.py      # terminal 1: the API (in-memory) with a bootstrap admin
    python scripts/dev_seed.py     # terminal 2: seeds, then keeps the simulated agents running

Signs in as the bootstrap admin from .fleetcontrol-dev-credentials.json and creates one demo person per
role (their one-time passwords go into the same git-ignored file). Creates three instances: staging and
production with a paired, simulated Fleet Control Agent, and a lab instance connected API-only. Uploads
the example AML blueprint, imports live profiles, applies v3 to staging, then changes two fields "by hand"
on staging and scans, so there is drift to resolve. Dana (Fleet Architect) creates a production plan that
waits for two approvals from Admin or Approver. The simulated agents keep answering jobs (import, drift
scan, policy push, apply) against an in-memory Hermes until you stop the script, so the screens work end
to end. `--once` seeds and exits (queued jobs then stay queued).
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "packages", "blueprint_schema"), os.path.join(ROOT, "apps", "agent")]

from dev_api import CREDENTIALS, save_credentials  # noqa: E402
from fleetcontrol_blueprint import load_blueprint  # noqa: E402
from fleetctl_agent.hermes_local import diff_managed  # noqa: E402

PEOPLE = [
    ("dana.whitfield@fleetcontrol.local", "Dana Whitfield", "fleet_architect"),
    ("sam.ortiz@fleetcontrol.local", "Sam Ortiz", "operator"),
    ("marcus.okafor@fleetcontrol.local", "Marcus Okafor", "approver"),
    ("lena.berg@fleetcontrol.local", "Lena Berg", "approver"),
    ("riya.shah@fleetcontrol.local", "Riya Shah", "viewer"),
]

BASE = os.environ.get("FLEETCONTROL_URL", "http://127.0.0.1:8080")
EXAMPLE = os.path.join(ROOT, "packages", "blueprint_schema", "examples", "aml-investigation.yaml")
AGENT_VERSION = "0.1.0"
REPORT = {
    "hermes_version": "0.21.2", "config_version": 44,
    "surfaces": {"dashboard": "loopback", "api": "ok", "cli": "ok"},
    "plugins": {"fleetcontrol": "enabled", "langfuse": "not enabled"},
    "dashboard_auth_required": False, "notes": [],
    "capabilities": ["runs", "sessions", "profiles.read", "profiles.write", "hooks", "policy.enforce"],
}


def call(method: str, path: str, body=None, token: str | None = None, query: dict | None = None, timeout: float = 40, opener=None):
    url = BASE + path + ("?" + urllib.parse.urlencode(query) if query else "")
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Fleet-Control", "1")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with (opener.open(req, timeout=timeout) if opener else urllib.request.urlopen(req, timeout=timeout)) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


class Session:
    """A signed-in person, like the web client: a cookie jar plus the X-Fleet-Control header."""

    def __init__(self, email: str, password: str):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.me = self.call("POST", "/api/v1/auth/login", {"email": email, "password": password}, timeout=10)

    def call(self, method: str, path: str, body=None, query: dict | None = None, timeout: float = 40):
        return call(method, path, body, query=query, timeout=timeout, opener=self.opener)


def soul_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256((text.rstrip() + "\n").encode("utf-8")).hexdigest()


class SimulatedAgent(threading.Thread):
    """Answers the jobs a real fleetctl-agent would, against an in-memory Hermes (``profiles``)."""

    def __init__(self, instance_id: str, token: str, profiles: dict, souls: dict):
        super().__init__(daemon=True, name=f"agent-{instance_id}")
        self.iid, self.token, self.profiles, self.souls = instance_id, token, profiles, souls

    def run(self) -> None:
        while True:
            try:
                job = call("GET", f"/agent/v1/instances/{self.iid}/jobs/next", token=self.token)
                if not job:
                    continue
                result = self.handle(job["kind"], job.get("params") or {})
                call("POST", f"/agent/v1/jobs/{job['id']}/result", result, token=self.token)
                print(f"  [{self.iid}] {job['kind']} -> {'ok' if result.get('ok') else 'failed'}", flush=True)
            except (urllib.error.URLError, OSError):
                time.sleep(3)

    def handle(self, kind: str, p: dict) -> dict:
        if kind == "import_profiles":
            return {"ok": True, "profiles": {n: {**s, "soul_text": self.souls.get(n, "")} for n, s in self.profiles.items()}}
        if kind == "drift_scan":
            drift = {}
            for name, desired in (p.get("managed") or {}).items():
                if name not in self.profiles:
                    drift[name] = [{"field": "profile", "blueprint": "present", "live": "missing (not on this instance)"}]
                elif diffs := diff_managed(desired, self.profiles[name]):
                    drift[name] = diffs
            return {"ok": True, "drift": drift, "scanned": list(p.get("managed") or {})}
        if kind == "push_policy":
            return {"ok": True, "written": sorted((p.get("profiles") or {}).keys())}
        if kind == "apply":
            results = []
            for ch in p.get("changes") or []:
                self.apply_op(ch)
                results.append({"op": ch["op"], "profile": ch.get("profile"), "ok": True})
            snap = f"/home/hermes/.fleetctl-agent/snapshots/hermes-snapshot-{int(time.time())}.tar.gz"
            return {"ok": True, "snapshot": snap, "results": results}
        return {"ok": True}

    def apply_op(self, ch: dict) -> None:
        name = ch["profile"]
        prof = self.profiles.setdefault(name, {"description": "", "model": {"provider": None, "name": None},
                                               "soul_sha256": soul_hash(""), "skills": [], "toolsets": [], "mcps": []})
        if ch["op"] == "ensure_profile":
            prof["description"] = ch.get("description", "")
            prof["model"] = {"provider": ch["provider"], "name": ch["model"]}
        elif ch["op"] == "write_soul":
            self.souls[name] = ch["content"]
            prof["soul_sha256"] = soul_hash(ch["content"])
        elif ch["op"] in ("set_skill", "set_toolset"):
            key, item = ("skills", ch["skill"]) if ch["op"] == "set_skill" else ("toolsets", ch["toolset"])
            items = set(prof[key])
            (items.add if ch.get("enabled", True) else items.discard)(item)
            prof[key] = sorted(items)
        elif ch["op"] in ("sync_skills", "sync_toolsets"):
            key = "skills" if ch["op"] == "sync_skills" else "toolsets"
            prof[key] = sorted(ch.get(key) or [])


def wait(check, what: str, timeout: float = 30) -> None:
    t0 = time.time()
    while not check():
        if time.time() - t0 > timeout:
            sys.exit(f"timed out waiting for {what}")
        time.sleep(0.3)


def main() -> None:
    if not os.path.exists(CREDENTIALS):
        sys.exit(f"{CREDENTIALS} not found. Start the API with scripts/dev_api.py first; it creates the admin.")
    with open(CREDENTIALS, encoding="utf-8") as f:
        creds = json.load(f)
    try:
        admin = Session(creds["admin"]["email"], creds["admin"]["password"])
    except urllib.error.HTTPError as exc:
        sys.exit(f"Signing in as {creds['admin']['email']} failed ({exc.code}). Was the API started with scripts/dev_api.py?")
    except (urllib.error.URLError, OSError) as exc:
        sys.exit(f"Fleet Control API not reachable at {BASE} ({exc}). Start it: python scripts/dev_api.py")
    if admin.call("GET", "/api/v1/instances"):
        sys.exit("The API already has data (it persists in .fleetcontrol-dev.db). "
                 "For a clean demo, restart it with: python scripts/dev_api.py --fresh")

    creds["people"] = {}
    for email, name, role in PEOPLE:
        created = admin.call("POST", "/api/v1/users", {"email": email, "name": name, "role": role})
        creds["people"][email] = {"name": name, "role": role, "password": created["password"]}
    save_credentials(creds)
    print(f"created {len(PEOPLE)} demo people (one per role); passwords in {CREDENTIALS}", flush=True)

    def instance(iid: str) -> dict:
        return admin.call("GET", f"/api/v1/instances/{iid}")

    bp = load_blueprint(EXAMPLE)
    managed = bp.managed_fields()
    souls = {a.profile_name: a.soul.render() for a in bp.agents}
    with open(EXAMPLE, encoding="utf-8") as f:
        admin.call("POST", "/api/v1/blueprints", {"yaml": f.read()})

    def live_copy() -> dict:
        return json.loads(json.dumps(managed))

    staging = live_copy()  # imported before v3: no challenger yet, an older skill set, one unmanaged bot
    staging.pop("challenger")
    staging["sanctions-screener"]["skills"] = ["legacy-pep-check", "name-matching"]
    staging["research-bot"] = {"description": "Ad-hoc research helper", "model": {"provider": "openai", "name": "gpt-5"},
                               "soul_sha256": soul_hash("You help with research."), "skills": ["web-research"], "toolsets": ["web"], "mcps": []}
    prod = live_copy()
    prod["ownership-tracer"]["model"] = {"provider": "local", "name": "llama-4-8b"}

    agents: dict[str, SimulatedAgent] = {}
    for iid, env, live in (("hermes-staging-eu-01", "staging", staging), ("hermes-prod-eu-01", "production", prod)):
        created = admin.call("POST", "/api/v1/instances", {"id": iid, "environment": env, "mode": "agent"})
        token = call("POST", "/agent/v1/pair", {"instance_id": iid, "agent_version": AGENT_VERSION, "report": REPORT},
                     token=created["pairing_token"])["agent_token"]
        agents[iid] = SimulatedAgent(iid, token, live, dict(souls))
        agents[iid].start()
    admin.call("POST", "/api/v1/instances", {"id": "hermes-lab-01", "environment": "lab", "mode": "api-only"})
    print("instances created; importing live profiles", flush=True)

    for iid in agents:
        admin.call("POST", f"/api/v1/instances/{iid}/import")
        wait(lambda iid=iid: instance(iid)["live_profile_count"] > 0, f"import on {iid}")

    stg = "hermes-staging-eu-01"
    plan = admin.call("POST", "/api/v1/plans", {"blueprint": bp.metadata.name, "version": bp.metadata.version, "instance_id": stg})
    admin.call("POST", f"/api/v1/plans/{plan['id']}/apply")
    wait(lambda: admin.call("GET", f"/api/v1/plans/{plan['id']}")["status"] == "applied", "apply on staging")
    print(f"applied {bp.metadata.name} v{bp.metadata.version} to {stg} ({plan['id']})", flush=True)

    # someone edits staging by hand, outside Fleet Control
    live = agents[stg].profiles
    live["sanctions-screener"]["skills"] = sorted(set(live["sanctions-screener"]["skills"]) | {"quick-lookup"})
    live["ownership-tracer"]["model"] = {"provider": "local", "name": "llama-4-8b"}
    admin.call("POST", f"/api/v1/instances/{stg}/drift-scan", query={"blueprint": bp.metadata.name, "version": bp.metadata.version})
    wait(lambda: instance(stg)["open_drift"] > 0, "drift scan on staging")

    dana_email = PEOPLE[0][0]
    dana = Session(dana_email, creds["people"][dana_email]["password"])
    prod_plan = dana.call("POST", "/api/v1/plans", {"blueprint": bp.metadata.name, "version": bp.metadata.version, "instance_id": "hermes-prod-eu-01"})
    print(f"production plan {prod_plan['id']} by {dana_email} waits for {prod_plan['approvals_required']} approvals (Admin or Approver)", flush=True)
    print(f"\nSeeded. Open the web client (npm run dev in apps/web) and sign in; credentials are in {CREDENTIALS}.", flush=True)

    if "--once" in sys.argv:
        return
    print("Simulated agents are running (heartbeats every 20 s, jobs answered as they arrive). Ctrl+C to stop.", flush=True)
    try:
        while True:
            for iid, agent in agents.items():
                try:
                    call("POST", f"/agent/v1/instances/{iid}/heartbeat", {"agent_version": AGENT_VERSION, "report": REPORT}, token=agent.token, timeout=10)
                except (urllib.error.URLError, OSError):
                    pass
            time.sleep(20)
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
