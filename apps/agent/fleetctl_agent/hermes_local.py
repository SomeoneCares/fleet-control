"""Local Hermes adapter used by the fleetctl-agent daemon.

Talks to the Hermes instance on the same host through the two surfaces that exist
(spike addendum, S1–S3, S7):

* the ``hermes`` CLI for profile lifecycle;
* the web dashboard backend on **loopback** for per-profile reads and writes, authenticated
  with the session token the daemon sets itself via ``HERMES_DASHBOARD_SESSION_TOKEN``
  (header ``X-Hermes-Session``; legacy ``Authorization: Bearer`` also accepted);
* the ``/v1`` API server for discovery, runs and sessions (``API_SERVER_KEY`` bearer).

Route names are pinned in ``ROUTES`` so a Hermes release that moves one fails loudly in
the compatibility check instead of silently in production. All numbers refer to
hermes-agent 0.21.2.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

# Dashboard backend routes (hermes_cli/web_routers/*). Verified by reading the source; the
# request/response bodies still need capturing on a real host (addendum §4).
ROUTES = {
    "status": ("GET", "/api/status"),  # unauthenticated: version, release_date, config_version
    "profiles.list": ("GET", "/api/profiles"),
    "profiles.create": ("POST", "/api/profiles"),  # ProfileCreate{name, clone_from, provider, model, mcp_servers, keep_skills}
    "profiles.delete": ("DELETE", "/api/profiles/{name}"),
    "profiles.soul.get": ("GET", "/api/profiles/{name}/soul"),
    "profiles.soul.put": ("PUT", "/api/profiles/{name}/soul"),  # {content}
    "profiles.model.put": ("PUT", "/api/profiles/{name}/model"),
    "profiles.description.put": ("PUT", "/api/profiles/{name}/description"),  # {description}
    "config.raw.get": ("GET", "/api/config/raw?profile={name}"),
    "skills.list": ("GET", "/api/skills?profile={name}"),
    "skills.toggle": ("PUT", "/api/skills/toggle?profile={name}"),  # {name, enabled, profile}
    "toolsets.list": ("GET", "/api/tools/toolsets?profile={name}"),
    "toolsets.toggle": ("PUT", "/api/tools/toolsets/{toolset}?profile={name}"),  # {enabled, profile}
    "mcp.list": ("GET", "/api/mcp/servers?profile={name}"),
    "mcp.create": ("POST", "/api/mcp/servers?profile={name}"),
    "mcp.delete": ("DELETE", "/api/mcp/servers/{server}?profile={name}"),
    "messaging.platforms": ("GET", "/api/messaging/platforms?profile={name}"),
    "messaging.platform.put": ("PUT", "/api/messaging/platforms/{platform}?profile={name}"),
    "webhooks.create": ("POST", "/api/webhooks?profile={name}"),  # deliver_only routes for delivery rules
    "cron.list": ("GET", "/api/cron/jobs?profile=all"),
}

API_ROUTES = {
    "health": ("GET", "/health"),  # version lives here, not on /v1/capabilities
    "capabilities": ("GET", "/v1/capabilities"),
    "runs.create": ("POST", "/v1/runs"),
    "runs.get": ("GET", "/v1/runs/{run_id}"),
    "runs.events": ("GET", "/v1/runs/{run_id}/events"),
    "sessions.messages": ("GET", "/api/sessions/{session_id}/messages"),  # after-the-fact evidence: full tool_calls
    "sessions.list": ("GET", "/api/sessions?include_children=true"),
}


class HermesLocalError(RuntimeError):
    pass


@dataclass
class HermesLocalConfig:
    hermes_bin: str = field(default_factory=lambda: shutil.which("hermes") or "hermes")
    dashboard_url: str = "http://127.0.0.1:9119"
    dashboard_token: Optional[str] = field(default_factory=lambda: os.environ.get("HERMES_DASHBOARD_SESSION_TOKEN"))
    api_url: str = "http://127.0.0.1:8642"
    api_key: Optional[str] = field(default_factory=lambda: os.environ.get("API_SERVER_KEY"))
    hermes_home: str = field(default_factory=lambda: os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes"))
    timeout: float = 15.0


class HermesLocal:
    def __init__(self, cfg: Optional[HermesLocalConfig] = None):
        self.cfg = cfg or HermesLocalConfig()

    # ------------------------------------------------------------------ http plumbing

    def _request(self, base: str, method: str, path: str, body: Optional[dict] = None, headers: Optional[dict] = None) -> Any:
        url = base.rstrip("/") + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise HermesLocalError(f"{method} {path} -> {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise HermesLocalError(f"{method} {path} unreachable: {e.reason}") from e

    def dashboard(self, route: str, body: Optional[dict] = None, **params: str) -> Any:
        method, path = ROUTES[route]
        headers = {}
        if self.cfg.dashboard_token:
            headers["X-Hermes-Session"] = self.cfg.dashboard_token
        return self._request(self.cfg.dashboard_url, method, path.format(**params), body, headers)

    def api(self, route: str, body: Optional[dict] = None, **params: str) -> Any:
        method, path = API_ROUTES[route]
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}
        return self._request(self.cfg.api_url, method, path.format(**params), body, headers)

    def cli(self, *args: str, profile: Optional[str] = None, timeout: float = 120) -> str:
        cmd = [self.cfg.hermes_bin]
        if profile:
            cmd += ["-p", profile]
        cmd += list(args)
        env = dict(os.environ, HERMES_HOME=self.cfg.hermes_home)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        if proc.returncode != 0:
            raise HermesLocalError(f"hermes {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()[:500]}")
        return proc.stdout

    # ------------------------------------------------------------------ discovery

    def capability_report(self) -> dict:
        """What Fleet Control can do on this host. Drives the Instances screen's honest panel."""
        report: dict[str, Any] = {"hermes_version": None, "surfaces": {}, "plugins": {}, "notes": []}
        try:
            st = self.dashboard("status")
            report["hermes_version"] = st.get("version")
            report["config_version"] = st.get("config_version")
            report["surfaces"]["dashboard"] = "loopback"
        except HermesLocalError as e:
            report["surfaces"]["dashboard"] = "unreachable"
            report["notes"].append(f"dashboard: {e}")
        try:
            h = self.api("health")
            report["hermes_version"] = report["hermes_version"] or h.get("version")
            caps = self.api("capabilities")
            report["surfaces"]["api"] = "ok"
            report["api_features"] = caps.get("features", {})
        except HermesLocalError as e:
            report["surfaces"]["api"] = "unreachable"
            report["notes"].append(f"api: {e}")
        report["surfaces"]["cli"] = "ok" if shutil.which(self.cfg.hermes_bin) or os.path.exists(self.cfg.hermes_bin) else "missing"
        plugin_dir = os.path.join(self.cfg.hermes_home, "plugins", "fleetcontrol")
        report["plugins"]["fleetcontrol"] = "installed" if os.path.isdir(plugin_dir) else "missing"
        report["plugins"]["langfuse"] = "enabled" if "observability/langfuse" in self._enabled_plugins() else "not enabled"
        # Capability ids consumed by blueprint.requires.capabilities
        caps_out = ["runs", "sessions", "profiles.read"] if report["surfaces"].get("api") == "ok" else []
        if report["surfaces"].get("dashboard") == "loopback" or report["surfaces"]["cli"] == "ok":
            caps_out += ["profiles.write"]
        if report["plugins"]["fleetcontrol"] == "installed":
            caps_out += ["hooks", "policy.enforce"]
        report["capabilities"] = caps_out
        return report

    def _enabled_plugins(self) -> list[str]:
        try:
            with open(os.path.join(self.cfg.hermes_home, "config.yaml"), "r", encoding="utf-8") as f:
                import yaml

                cfg = yaml.safe_load(f) or {}
            return list((cfg.get("plugins") or {}).get("enabled") or [])
        except Exception:
            return []

    # ------------------------------------------------------------------ live state (for import + drift)

    def live_profile_state(self, name: str) -> dict:
        """Live values of the managed fields for one profile, shaped like Blueprint.managed_fields()."""
        import hashlib

        soul = self.dashboard("profiles.soul.get", name=name) or {}
        soul_text = (soul.get("content") or "").rstrip() + "\n"
        prof = next((p for p in (self.dashboard("profiles.list") or []) if p.get("name") == name), {})
        skills = [s["name"] for s in (self.dashboard("skills.list", name=name) or []) if s.get("enabled")]
        toolsets = [t["name"] for t in (self.dashboard("toolsets.list", name=name) or []) if t.get("enabled")]
        mcps = list((self.dashboard("mcp.list", name=name) or {}).keys()) if isinstance(self.dashboard("mcp.list", name=name), dict) else [
            m.get("name") for m in (self.dashboard("mcp.list", name=name) or [])
        ]
        return {
            "description": prof.get("description"),
            "model": {"provider": prof.get("provider"), "name": prof.get("model")},
            "soul_sha256": "sha256:" + hashlib.sha256(soul_text.encode("utf-8")).hexdigest(),
            "skills": sorted(skills),
            "toolsets": sorted(toolsets),
            "mcps": sorted(m for m in mcps if m),
        }

    # ------------------------------------------------------------------ writes (managed fields only)

    def ensure_profile(self, name: str, description: str, provider: str, model: str, clone_from: Optional[str] = None) -> None:
        existing = {p.get("name") for p in (self.dashboard("profiles.list") or [])}
        if name not in existing:
            body = {"name": name, "description": description, "provider": provider, "model": model}
            if clone_from:
                body["clone_from"] = clone_from
            try:
                self.dashboard("profiles.create", body)
            except HermesLocalError:
                # dashboard down or route changed: fall back to the CLI, same on-disk result
                args = ["profile", "create", name]
                if clone_from:
                    args += ["--clone-from", clone_from]
                self.cli(*args)
        else:
            self.dashboard("profiles.description.put", {"description": description}, name=name)
            self.dashboard("profiles.model.put", {"provider": provider, "model": model}, name=name)

    def write_soul(self, name: str, content: str) -> None:
        self.dashboard("profiles.soul.put", {"content": content}, name=name)

    def set_skill(self, name: str, skill: str, enabled: bool) -> None:
        self.dashboard("skills.toggle", {"name": skill, "enabled": enabled, "profile": name}, name=name)

    def set_toolset(self, name: str, toolset: str, enabled: bool) -> None:
        self.dashboard("toolsets.toggle", {"enabled": enabled, "profile": name}, name=name, toolset=toolset)

    def snapshot(self, dest_dir: str) -> str:
        """Tar the profile directories before an apply. Returns the archive path."""
        import tarfile
        import time

        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, f"hermes-snapshot-{int(time.time())}.tar.gz")
        with tarfile.open(path, "w:gz") as tar:
            for entry in ("config.yaml", "SOUL.md", "profiles", "skills", "mcp"):
                p = os.path.join(self.cfg.hermes_home, entry)
                if os.path.exists(p):
                    tar.add(p, arcname=entry)
        return path


# ---------------------------------------------------------------------- drift


def diff_managed(desired: dict, live: dict) -> list[dict]:
    """Field-level diff between Blueprint.managed_fields()[profile] and live_profile_state()."""
    out = []
    for key in ("description", "model", "soul_sha256", "skills", "toolsets", "mcps"):
        d, l = desired.get(key), live.get(key)
        if d != l:
            out.append({"field": key, "blueprint": d, "live": l})
    return out
