"""fleetctl-agent: the host daemon of the Fleet Control Agent.

Responsibilities (build document §5.2):
- accept evidence events from the in-process Hermes plugin on a local socket, buffer them,
  and relay them to Fleet Control;
- hold ONE outbound connection to Fleet Control (WebSocket, mTLS in production) and execute
  the jobs it receives: capability report, import, plan-apply for managed fields, drift scan,
  policy push, snapshot, test run;
- never expose an inbound network port.

This is the Slice 1 skeleton: the socket server, job loop and job handlers are real; the
transport to Fleet Control is a thin interface with an HTTP long-poll implementation so it
can run against the API scaffold today and be swapped for WebSocket without touching jobs.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .hermes_local import HermesLocal, HermesLocalConfig, diff_managed

logger = logging.getLogger("fleetctl-agent")

AGENT_VERSION = "0.1.0"


@dataclass
class AgentConfig:
    control_plane_url: str = field(default_factory=lambda: os.environ.get("FLEETCONTROL_URL", "http://127.0.0.1:8080"))
    instance_id: str = field(default_factory=lambda: os.environ.get("FLEETCONTROL_INSTANCE_ID", socket.gethostname()))
    pairing_token: Optional[str] = field(default_factory=lambda: os.environ.get("FLEETCONTROL_PAIRING_TOKEN"))
    agent_token: Optional[str] = field(default_factory=lambda: os.environ.get("FLEETCONTROL_AGENT_TOKEN"))
    socket_path: str = field(default_factory=lambda: os.environ.get("FLEETCONTROL_AGENT_SOCKET", ""))
    state_dir: str = field(default_factory=lambda: os.environ.get("FLEETCONTROL_STATE_DIR", os.path.expanduser("~/.fleetctl-agent")))
    heartbeat_seconds: int = 30
    drift_scan_seconds: int = 300
    hermes: HermesLocalConfig = field(default_factory=HermesLocalConfig)

    def resolved_socket(self) -> str:
        if self.socket_path:
            return self.socket_path
        if os.name == "nt":
            return "tcp://127.0.0.1:47831"
        return os.path.join(self.hermes.hermes_home, "fleetcontrol", "agent.sock")


# ----------------------------------------------------------------------------- transport


class ControlPlane:
    """Minimal HTTP transport. POST events/heartbeats; GET jobs (long-poll); POST job results."""

    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg

    def _call(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 35) -> Any:
        url = self.cfg.control_plane_url.rstrip("/") + path
        data = json.dumps(body, default=str).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        token = self.cfg.agent_token or self.cfg.pairing_token
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None

    def pair(self, report: dict) -> str:
        out = self._call("POST", "/agent/v1/pair", {"instance_id": self.cfg.instance_id, "agent_version": AGENT_VERSION, "report": report})
        return out["agent_token"]

    def heartbeat(self, report: dict) -> None:
        self._call("POST", f"/agent/v1/instances/{self.cfg.instance_id}/heartbeat", {"agent_version": AGENT_VERSION, "report": report}, timeout=10)

    def push_events(self, events: list[dict]) -> None:
        self._call("POST", f"/agent/v1/instances/{self.cfg.instance_id}/events", {"events": events}, timeout=15)

    def next_job(self) -> Optional[dict]:
        return self._call("GET", f"/agent/v1/instances/{self.cfg.instance_id}/jobs/next", timeout=35)

    def job_result(self, job_id: str, result: dict) -> None:
        self._call("POST", f"/agent/v1/jobs/{job_id}/result", result, timeout=15)


# ----------------------------------------------------------------------------- plugin socket


class PluginSocketServer(threading.Thread):
    """Receives newline-delimited JSON events from the fleetcontrol Hermes plugin."""

    def __init__(self, target: str, sink: "queue.Queue[dict]"):
        super().__init__(name="plugin-socket", daemon=True)
        self.target, self.sink = target, sink

    def run(self) -> None:
        if self.target.startswith("tcp://"):
            host, port = self.target[6:].rsplit(":", 1)
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((host, int(port)))
        else:
            os.makedirs(os.path.dirname(self.target), exist_ok=True)
            if os.path.exists(self.target):
                os.unlink(self.target)
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(self.target)
            os.chmod(self.target, 0o600)
        srv.listen(8)
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        buf = b""
        with conn:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        self.sink.put(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("bad plugin event line dropped")


# ----------------------------------------------------------------------------- jobs


class Jobs:
    """Job handlers. Each returns a JSON-serialisable result dict."""

    def __init__(self, cfg: AgentConfig, hermes: HermesLocal):
        self.cfg, self.hermes = cfg, hermes
        self.handlers: dict[str, Callable[[dict], dict]] = {
            "capability_report": self.capability_report,
            "import_profiles": self.import_profiles,
            "drift_scan": self.drift_scan,
            "apply": self.apply,
            "push_policy": self.push_policy,
            "snapshot": self.snapshot,
            "run_test": self.run_test,
        }

    def dispatch(self, job: dict) -> dict:
        kind = job.get("kind")
        h = self.handlers.get(kind)
        if not h:
            return {"ok": False, "error": f"unknown job kind {kind!r}", "agent_version": AGENT_VERSION}
        try:
            out = h(job.get("params") or {})
            out.setdefault("ok", True)
            return out
        except Exception as exc:  # report, never crash the loop
            logger.exception("job %s failed", kind)
            return {"ok": False, "error": str(exc)}

    def capability_report(self, p: dict) -> dict:
        return {"report": self.hermes.capability_report()}

    def import_profiles(self, p: dict) -> dict:
        profiles = self.hermes.profiles()
        out = {}
        for prof in profiles:
            name = prof.get("name")
            if not name:
                continue
            state = self.hermes.live_profile_state(name, profiles)
            soul = self.hermes.dashboard("profiles.soul.get", name=name) or {}
            state["soul_text"] = soul.get("content", "")
            out[name] = state
        return {"profiles": out}

    def drift_scan(self, p: dict) -> dict:
        """params: {"managed": {profile: managed_fields}} from the applied blueprint version."""
        managed: dict = p.get("managed") or {}
        drift = {}
        for name, desired in managed.items():
            try:
                live = self.hermes.live_profile_state(name)
            except Exception as exc:
                drift[name] = [{"field": "profile", "blueprint": "present", "live": f"missing ({exc})"}]
                continue
            d = diff_managed(desired, live)
            if d:
                drift[name] = d
        return {"drift": drift, "scanned": list(managed.keys())}

    def apply(self, p: dict) -> dict:
        """params: {"changes": [ {op, profile, ...} ], "snapshot": true}.
        Ops: ensure_profile, write_soul, set_skill, set_toolset, sync_skills, sync_toolsets (enable exactly
        the listed ones; used for new profiles). Order is preserved; first failure stops."""
        results = []
        snap = None
        if p.get("snapshot", True):
            snap = self.hermes.snapshot(os.path.join(self.cfg.state_dir, "snapshots"))
        for ch in p.get("changes") or []:
            op = ch.get("op")
            try:
                if op == "ensure_profile":
                    self.hermes.ensure_profile(ch["profile"], ch.get("description", ""), ch["provider"], ch["model"], ch.get("clone_from"))
                elif op == "write_soul":
                    self.hermes.write_soul(ch["profile"], ch["content"])
                elif op == "set_skill":
                    self.hermes.set_skill(ch["profile"], ch["skill"], bool(ch.get("enabled", True)))
                elif op == "set_toolset":
                    self.hermes.set_toolset(ch["profile"], ch["toolset"], bool(ch.get("enabled", True)))
                elif op == "sync_skills":
                    self.hermes.sync_skills(ch["profile"], ch.get("skills") or [])
                elif op == "sync_toolsets":
                    self.hermes.sync_toolsets(ch["profile"], ch.get("toolsets") or [])
                else:
                    raise ValueError(f"unknown op {op!r}")
                results.append({"op": op, "profile": ch.get("profile"), "ok": True})
            except Exception as exc:
                results.append({"op": op, "profile": ch.get("profile"), "ok": False, "error": str(exc)})
                return {"ok": False, "snapshot": snap, "results": results, "stopped_at": len(results) - 1}
        return {"snapshot": snap, "results": results}

    def push_policy(self, p: dict) -> dict:
        """params: {"profiles": {profile_name: policy_json}} — written where the plugin reads them."""
        written = []
        for name, policy in (p.get("profiles") or {}).items():
            home = self.hermes.cfg.hermes_home if name == "default" else os.path.join(self.hermes.cfg.hermes_home, "profiles", name)
            d = os.path.join(home, "fleetcontrol")
            os.makedirs(d, exist_ok=True)
            tmp = os.path.join(d, "policy.json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(policy, f, indent=2)
            os.replace(tmp, os.path.join(d, "policy.json"))
            written.append(name)
        return {"written": written}

    def snapshot(self, p: dict) -> dict:
        return {"snapshot": self.hermes.snapshot(os.path.join(self.cfg.state_dir, "snapshots"))}

    def run_test(self, p: dict) -> dict:
        """Submit a test scenario as a /v1 run; evidence arrives via the plugin. Returns run + session ids."""
        run = self.hermes.api("runs.create", {"input": p["scenario"], "metadata": {"fleetcontrol_test": p.get("test_id")}})
        return {"run_id": run.get("run_id"), "status": run.get("status")}


# ----------------------------------------------------------------------------- main loop


class AgentDaemon:
    def __init__(self, cfg: Optional[AgentConfig] = None):
        self.cfg = cfg or AgentConfig()
        self.hermes = HermesLocal(self.cfg.hermes)
        self.cp = ControlPlane(self.cfg)
        self.jobs = Jobs(self.cfg, self.hermes)
        self.events: "queue.Queue[dict]" = queue.Queue(maxsize=50_000)
        os.makedirs(self.cfg.state_dir, exist_ok=True)

    def _pair_if_needed(self) -> None:
        token_file = os.path.join(self.cfg.state_dir, "agent.token")
        if not self.cfg.agent_token and os.path.exists(token_file):
            self.cfg.agent_token = open(token_file).read().strip()
        if not self.cfg.agent_token:
            if not self.cfg.pairing_token:
                raise SystemExit("fleetctl-agent: not paired; set FLEETCONTROL_PAIRING_TOKEN from the Connect instance drawer")
            delay = 5
            while True:
                try:
                    token = self.cp.pair(self.hermes.capability_report())
                    break
                except urllib.error.HTTPError:
                    raise  # Fleet Control answered and refused the token; retrying cannot help
                except OSError as exc:  # URLError included: not reachable yet, keep the token and wait
                    logger.warning("Fleet Control unreachable at %s (%s); retrying pairing in %ds",
                                   self.cfg.control_plane_url, getattr(exc, "reason", exc), delay)
                    time.sleep(delay)
                    delay = min(delay * 2, 60)
            with open(token_file, "w") as f:
                f.write(token)
            os.chmod(token_file, 0o600)
            self.cfg.agent_token = token
            logger.info("paired with Fleet Control as %s", self.cfg.instance_id)

    def _event_relay(self) -> None:
        while True:
            batch = [self.events.get()]
            t0 = time.time()
            while len(batch) < 500 and time.time() - t0 < 1.0:
                try:
                    batch.append(self.events.get(timeout=0.2))
                except queue.Empty:
                    break
            try:
                self.cp.push_events(batch)
            except Exception as exc:
                logger.warning("event relay failed (%s); re-queueing %d", exc, len(batch))
                for e in batch:
                    try:
                        self.events.put_nowait(e)
                    except queue.Full:
                        break
                time.sleep(5)

    def _heartbeat(self) -> None:
        while True:
            try:
                self.cp.heartbeat(self.hermes.capability_report())
            except Exception as exc:
                logger.warning("heartbeat failed: %s", exc)
            time.sleep(self.cfg.heartbeat_seconds)

    def run(self) -> None:
        self._pair_if_needed()
        PluginSocketServer(self.cfg.resolved_socket(), self.events).start()
        threading.Thread(target=self._event_relay, name="event-relay", daemon=True).start()
        threading.Thread(target=self._heartbeat, name="heartbeat", daemon=True).start()
        logger.info("fleetctl-agent %s running; instance=%s socket=%s", AGENT_VERSION, self.cfg.instance_id, self.cfg.resolved_socket())
        while True:
            try:
                job = self.cp.next_job()
            except Exception as exc:
                logger.warning("job poll failed: %s", exc)
                time.sleep(5)
                continue
            if not job:
                continue
            result = self.jobs.dispatch(job)
            try:
                self.cp.job_result(job["id"], result)
            except Exception as exc:
                logger.warning("job result delivery failed: %s", exc)


def main() -> None:
    logging.basicConfig(level=os.environ.get("FLEETCONTROL_LOG", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")
    AgentDaemon().run()


if __name__ == "__main__":
    main()
