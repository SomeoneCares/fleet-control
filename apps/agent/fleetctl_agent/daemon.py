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

from .hermes_local import HermesLocal, HermesLocalConfig, HermesLocalError, diff_managed, tool_calls_from_messages

logger = logging.getLogger("fleetctl-agent")

AGENT_VERSION = "0.1.0"

# Jobs that wait on a Hermes run (minutes) run on their own thread, so imports, applies and drift scans
# queued behind them are not held up.
BACKGROUND_JOBS = frozenset({"hermes_run", "run_test", "mcp_discover"})


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


# ----------------------------------------------------------------------------- endpoint diagnosis


def diagnose_endpoint(url: str, timeout: float = 5.0) -> str:
    """Why an MCP server's url does not answer, one layer at a time: name, TCP, TLS, HTTP. Sends no credentials.
    An HTTP answer of any status means the server is up, so the fault is past the connection."""
    import ssl
    import urllib.parse

    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        socket.getaddrinfo(host, port)
    except OSError as exc:
        return f"cannot resolve {host}: {exc.strerror or exc}"
    try:
        socket.create_connection((host, port), timeout=timeout).close()
    except OSError as exc:
        reason = "timed out" if isinstance(exc, (socket.timeout, TimeoutError)) else (exc.strerror or str(exc) or type(exc).__name__)
        return f"cannot connect to {host}:{port} ({reason}): the server or its ingress is down, not the credentials"
    cafile = os.environ.get("SSL_CERT_FILE")
    context = ssl.create_default_context(cafile=cafile if cafile and os.path.exists(cafile) else None)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout, context=context) as r:
            status, reason = r.status, r.reason
    except urllib.error.HTTPError as exc:
        status, reason = exc.code, exc.reason
    except urllib.error.URLError as exc:
        inner = exc.reason
        if isinstance(inner, ssl.SSLError):
            return (f"TLS with {host}:{port} failed ({getattr(inner, 'verify_message', None) or inner}); Hermes checks "
                    "certificates against the bundle in SSL_CERT_FILE, not the system store")
        return f"{host}:{port} accepts connections but the request failed: {inner}"
    except (TimeoutError, socket.timeout):
        return f"{host}:{port} accepts connections but sent no answer within {timeout:g} s"
    except OSError as exc:
        return f"{host}:{port} accepts connections but the request failed: {exc}"
    if status >= 500:
        return f"the endpoint answers HTTP {status} {reason}: the server behind it is failing, not the credentials"
    if status in (401, 403):
        # this check sends no credentials, so a protected server answers 401 even when the profile's login is fine
        return (f"the endpoint answers HTTP {status} {reason} to a request without credentials: it is up, so look at "
                "the profile's login (missing or expired) or the MCP handshake")
    return f"the endpoint answers HTTP {status} {reason}: it is up, so the failure is in the MCP handshake or the credentials"


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
            "hermes_run": self.hermes_run,
            "mcp_discover": self.mcp_discover,
            "mcp_write": self.mcp_write,
            "messaging_discover": self.messaging_discover,
            "channel_route": self.channel_route,
            "deliver_message": self.deliver_message,
            "webhooks_enable": self.webhooks_enable,
            "kanban_submit": self.kanban_submit,
            "kanban_read": self.kanban_read,
            "kanban_cancel": self.kanban_cancel,
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
        the listed ones; used for new profiles), copy_mcp (register a server with the configuration another
        profile of this host has for it; never its secrets) and remove_mcp. Order is preserved; first failure stops."""
        results = []
        snap = None
        if p.get("snapshot", True):
            snap = self.hermes.snapshot(os.path.join(self.cfg.state_dir, "snapshots"))
        for ch in p.get("changes") or []:
            op = ch.get("op")
            try:
                if op == "ensure_profile":
                    self._clear_policy_stub(ch["profile"])  # a folder left by an earlier agent would block the create
                    self.hermes.ensure_profile(ch["profile"], ch.get("description", ""), ch["provider"], ch["model"], ch.get("clone_from"))
                    self._install_pending_policy(ch["profile"])
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
                elif op == "copy_mcp":
                    done = self.hermes.mcp_copy(ch["profile"], ch["server"], ch.get("from_profile"))
                    results.append({"op": op, "profile": ch["profile"], "server": ch["server"], "ok": True, **done})
                    continue
                elif op == "remove_mcp":
                    if any(s.get("name") == ch["server"] for s in self.hermes.mcp_list(ch["profile"])):
                        self.hermes.mcp_remove(ch["profile"], ch["server"])
                else:
                    raise ValueError(f"unknown op {op!r}")
                results.append({"op": op, "profile": ch.get("profile"), "ok": True})
            except Exception as exc:
                results.append({"op": op, "profile": ch.get("profile"), "ok": False, "error": str(exc)})
                return {"ok": False, "snapshot": snap, "results": results, "stopped_at": len(results) - 1}
        return {"snapshot": snap, "results": results}

    def push_policy(self, p: dict) -> dict:
        """params: {"profiles": {profile_name: policy_json}} — written where the plugin reads them.

        A profile the apply has not created yet gets its policy held here and installed right after the profile
        exists: writing into ~/.hermes/profiles/<name>/ first would make that folder, and Hermes then refuses to
        create a profile whose folder already exists."""
        written, held = [], []
        real = {pr.get("name") for pr in self.hermes.profiles()}
        for name, policy in (p.get("profiles") or {}).items():
            if name != "default" and name not in real:
                self._clear_policy_stub(name)
                self._write_policy(self._pending_policy_path(name), policy)
                held.append(name)
                continue
            self._write_policy(os.path.join(self._profile_home(name), "fleetcontrol", "policy.json"), policy)
            written.append(name)
        return {"written": written, "held_until_created": held}

    def _profile_home(self, name: str) -> str:
        return self.hermes.cfg.hermes_home if name == "default" else os.path.join(self.hermes.cfg.hermes_home, "profiles", name)

    def _pending_policy_path(self, name: str) -> str:
        return os.path.join(self.cfg.state_dir, "pending-policy", f"{name}.json")

    @staticmethod
    def _write_policy(path: str, policy: dict) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(policy, f, indent=2)
        os.replace(tmp, path)

    def _clear_policy_stub(self, name: str) -> bool:
        """Remove a folder earlier agents left behind for a profile that was never created: it holds nothing but
        fleetcontrol/policy.json. Anything else in it, and it is left alone (it is not ours to delete)."""
        home = self._profile_home(name)
        if name == "default" or not os.path.isdir(home):
            return False
        entries = os.listdir(home)
        fc = os.path.join(home, "fleetcontrol")
        if entries != ["fleetcontrol"] or not os.path.isdir(fc) or set(os.listdir(fc)) - {"policy.json", "policy.json.tmp"}:
            return False
        for f in os.listdir(fc):
            os.remove(os.path.join(fc, f))
        os.rmdir(fc)
        os.rmdir(home)
        logger.info("removed the policy-only folder left for profile %s, so Hermes can create it", name)
        return True

    def _install_pending_policy(self, name: str) -> None:
        pending = self._pending_policy_path(name)
        if os.path.exists(pending):
            target = os.path.join(self._profile_home(name), "fleetcontrol", "policy.json")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.replace(pending, target)

    def snapshot(self, p: dict) -> dict:
        return {"snapshot": self.hermes.snapshot(os.path.join(self.cfg.state_dir, "snapshots"))}

    def mcp_discover(self, p: dict) -> dict:
        """params: {profiles: [...], probe: true}: the MCP servers of each profile and, for the enabled ones,
        the tools they expose (each probe connects to the server, so this job runs on its own thread)."""
        out: dict[str, list[dict]] = {}
        for profile in p.get("profiles") or []:
            servers = []
            for s in self.hermes.mcp_list(profile):
                row = {k: s[k] for k in ("name", "transport", "enabled", "url", "command", "auth", "tool_count") if k in s}
                if p.get("probe", True) and s.get("enabled", True) and s.get("name"):
                    probe = self.hermes.mcp_probe(profile, s["name"])
                    row["ok"] = bool(probe.get("ok"))
                    row["error"] = probe.get("error")
                    if not row["ok"] and not (row["error"] or "").strip():
                        # Hermes gives no text when the upstream never answers; say which layer failed
                        # rather than leave a blank that reads like a credential problem
                        row["error"] = diagnose_endpoint(s.get("url")) if s.get("url") else \
                            "Hermes reported no error text: the server process gave no answer"
                    row["tools"] = [{"name": t.get("name"), "description": (t.get("description") or "")[:200]}
                                    for t in (probe.get("tools") or [])][:100]
                servers.append(row)
            out[profile] = servers
        return {"servers": out, "at": time.time()}

    # ---- messaging: Fleet Control's routes are deliver_only webhooks whose secrets never leave this host

    def _route_secrets_path(self) -> str:
        return os.path.join(self.cfg.state_dir, "route-secrets.json")

    def _route_secrets(self) -> dict:
        try:
            with open(self._route_secrets_path(), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_route_secrets(self, secrets_: dict) -> None:
        os.makedirs(self.cfg.state_dir, exist_ok=True)
        path = self._route_secrets_path()
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(secrets_, f)
        os.replace(tmp, path)

    def messaging_discover(self, p: dict) -> dict:
        """The messaging platforms and the webhook platform's state; which Fleet Control routes this agent can
        sign for (it holds their secrets)."""
        state = self.hermes.messaging_state()
        state["gateway"] = self.hermes.gateway_runtime()
        held = set(self._route_secrets())
        for r in state["webhooks"]["routes"]:
            r["signable"] = r.get("name") in held
        return state

    def channel_route(self, p: dict) -> dict:
        """params: {action: create|remove, route, platform?, chat_id?}. Create replaces a route of the same name,
        since only the secret made here can sign for it."""
        import secrets as _secrets

        route, action = p["route"], p["action"]
        if not route.startswith("fc-"):
            raise ValueError("Fleet Control only manages its own routes (fc-…)")
        held = self._route_secrets()
        existing = {r.get("name") for r in self.hermes.messaging_state()["webhooks"]["routes"]}
        if route in existing:
            self.hermes.webhook_delete(route)
        held.pop(route, None)
        if action == "remove":
            self._save_route_secrets(held)
            return {"route": route, "removed": route in existing}
        secret = _secrets.token_urlsafe(32)
        summary = self.hermes.webhook_create(route, p["platform"], p.get("chat_id"), secret,
                                             f"Fleet Control channel {p.get('channel') or route} (deliver only)")
        held[route] = secret
        self._save_route_secrets(held)
        return {"route": route, "url": summary.get("url"), "created": True}

    def deliver_message(self, p: dict) -> dict:
        """params: {route, text, delivery_id, direct?, chat_id?}: post the text to this host's own route, signed; a direct
        route (chat_id template) gets the recipient in the payload. Hermes delivers it
        through the platform; the answer says whether the platform took it."""
        route = p["route"]
        secret = self._route_secrets().get(route)
        if not secret:
            return {"ok": False, "error": f"this agent holds no secret for {route}: recreate the channel's route"}
        hooks = self.hermes.messaging_state()["webhooks"]
        if not hooks["enabled"]:
            return {"ok": False, "error": "the webhook platform is off on this instance (Messaging → Enable webhooks)"}
        url = next((r.get("url") for r in hooks["routes"] if r.get("name") == route), None)
        if not url:
            return {"ok": False, "error": f"route {route} is not on this instance: recreate the channel's route"}
        payload = {"text": p["text"], "event_type": "fleetcontrol"}
        if p.get("direct"):
            chat_id = str(p.get("chat_id") or "").strip()
            if not chat_id or "{" in chat_id:
                # an empty chat_id makes Hermes fall back to the platform's home channel: never for a personal message
                return {"ok": False, "error": "a direct message needs its recipient's address; nothing was sent"}
            payload["chat_id"] = chat_id
        status, body = self.hermes.webhook_post(url, payload, secret, p["delivery_id"])
        state = body.get("status") if isinstance(body, dict) else None
        ok = status == 200 and state in ("delivered", "duplicate")
        out = {"ok": ok, "http_status": status, "status": state}
        if not ok:
            out["error"] = f"HTTP {status}: {body if isinstance(body, str) else json.dumps(body)[:300]}"
        return out

    def kanban_submit(self, p: dict) -> dict:
        """params: {board, tasks: [{key, title, body, assignee, parents, idempotency_key, tenant, max_runtime_seconds}]}."""
        return {"ids": self.hermes.kanban_submit(p["board"], p["tasks"])}

    def kanban_read(self, p: dict) -> dict:
        """params: {board, task_ids}."""
        return {"tasks": self.hermes.kanban_read(p["board"], p["task_ids"])}

    def kanban_cancel(self, p: dict) -> dict:
        return self.hermes.kanban_archive(p["board"], p["task_ids"])

    def webhooks_enable(self, p: dict) -> dict:
        """Turn the webhook platform on. Hermes restarts the gateway to start it."""
        return self.hermes.webhook_enable()

    def mcp_write(self, p: dict) -> dict:
        """params: {profile, action: add|remove|enable|disable, server?, config?}. Credentials are never sent
        through Fleet Control: a server is added by url or command only, and secrets stay on this host."""
        profile, action = p["profile"], p["action"]
        if action == "add":
            config = dict(p["config"])
            server = config["name"]
            out = self.hermes.mcp_add(profile, config)
        elif action == "remove":
            server, out = p["server"], self.hermes.mcp_remove(profile, p["server"])
        elif action in ("enable", "disable"):
            server, out = p["server"], self.hermes.mcp_set_enabled(profile, p["server"], action == "enable")
        else:
            raise ValueError(f"unknown MCP action {action!r}")
        return {"action": action, "profile": profile, "server": server, "result": out}

    def _ensure_key(self, profile: Optional[str]) -> list[str]:
        """A named profile needs its own API_SERVER_KEY for /p/<profile>/ runs; create one on this host when it
        has none (the profile was chosen for Fleet Control to run), and say so."""
        if profile not in (None, "", "default") and not self.hermes.profile_api_key(profile):
            self.hermes.ensure_profile_api_key(profile)
            return [f"gave profile {profile} its own API_SERVER_KEY (in its .env on this host)"]
        return []

    def run_test(self, p: dict) -> dict:
        """params: {profile, scenario, timeout?}: run a test scenario on a profile (Test Lab) and collect the evidence
        Fleet Control judges it by: the run's output, usage and duration, and every tool call in its session
        transcript. ``ok`` means the test ran; whether it passed is Fleet Control's call."""
        profile = p.get("profile")
        notes = self._ensure_key(profile)
        started = time.monotonic()
        out = self.hermes.run_agent(profile, p["scenario"], p.get("instructions"), float(p.get("timeout", 300)))
        out["duration_s"] = round(time.monotonic() - started, 1)
        out["tool_calls"], out["evidence"] = None, "none"
        if out.get("session_id"):
            try:
                out["tool_calls"] = tool_calls_from_messages(self.hermes.session_messages(profile, out["session_id"]))
                out["evidence"] = "transcript"
            except HermesLocalError as exc:
                out["evidence_error"] = str(exc)
        out["ok"] = True
        if notes:
            out["notes"] = notes
        return out

    def hermes_run(self, p: dict) -> dict:
        """params: {profile, input, instructions?, timeout?, transcript?}: one Hermes /v1 run, waited for (Fleet
        Architect, Ask the fleet). With ``transcript``, the run's tool calls come back too, from its session
        transcript, so Fleet Control can tell whether the agent used anything besides what it was given."""
        profile = p.get("profile")
        notes = self._ensure_key(profile)
        out = self.hermes.run_agent(profile, p["input"], p.get("instructions"), float(p.get("timeout", 600)))
        out["ok"] = out.get("status") == "completed"
        if p.get("transcript") and out["ok"]:
            if out.get("session_id"):
                try:
                    out["tool_calls"] = tool_calls_from_messages(self.hermes.session_messages(profile, out["session_id"]))
                except HermesLocalError as exc:
                    out["evidence_error"] = str(exc)
            else:
                out["evidence_error"] = "the run reported no session id"
        if notes:
            out["notes"] = notes
        return out


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
            if job.get("kind") in BACKGROUND_JOBS:
                threading.Thread(target=self._run_and_report, args=(job,), name=f"job-{job['id']}", daemon=True).start()
            else:
                self._run_and_report(job)

    def _run_and_report(self, job: dict) -> None:
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
