"""Local Hermes adapter used by the fleetctl-agent daemon.

Talks to the Hermes instance on the same host through the two surfaces that exist
(spike addendum, S1–S3, S7):

* the ``hermes`` CLI for profile lifecycle;
* the web dashboard backend on **loopback** for per-profile reads and writes, authenticated
  with the session token the daemon sets itself via ``HERMES_DASHBOARD_SESSION_TOKEN``
  (header ``X-Hermes-Session-Token``; ``Authorization: Bearer`` also accepted);
* the ``/v1`` API server for discovery, runs and sessions (``API_SERVER_KEY`` bearer).

Route names are pinned in ``ROUTES`` so a Hermes release that moves one fails loudly in
the compatibility check instead of silently in production. Request bodies and response
shapes were captured on a real hermes-agent 0.21.2 host: ``docs/dashboard-capture-0.21.2*.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# The header the dashboard checks (``_SESSION_HEADER_NAME`` in hermes_cli/web_server.py).
# A bare ``X-Hermes-Session`` is refused with 401 on 0.21.2 (see the capture's auth probe).
DASHBOARD_SESSION_HEADER = "X-Hermes-Session-Token"

# Skills Hermes keeps enabled whatever the config says (``ESSENTIAL_SKILLS`` in agent/skill_utils.py;
# the disabled list silently drops them). Fleet Control leaves them unmanaged: not in live state,
# not synced, never drift. scripts/hermes_compat_check.py fails when upstream's set changes.
HERMES_ESSENTIAL_SKILLS = frozenset({"hermes-agent"})

# Dashboard backend routes (hermes_cli/web_routers/*). Request bodies are the web_models.py
# classes named below; response shapes are as captured on 0.21.2.
ROUTES = {
    "status": ("GET", "/api/status"),  # unauthenticated: version, config_version, auth_required, profiles
    "profiles.list": ("GET", "/api/profiles"),  # {"profiles": [{name, model, provider, description, ...}]}
    # ProfileCreate{name, description, provider, model, clone_from} -> {ok, path, model_set, model_error, ...}.
    # clone_channels defaults to false, so bot credentials are never copied into a new profile.
    "profiles.create": ("POST", "/api/profiles"),
    "profiles.delete": ("DELETE", "/api/profiles/{name}"),  # -> {ok, path}
    "profiles.soul.get": ("GET", "/api/profiles/{name}/soul"),  # -> {content, exists}
    "profiles.soul.put": ("PUT", "/api/profiles/{name}/soul"),  # ProfileSoulUpdate{content} -> {ok}
    "profiles.model.put": ("PUT", "/api/profiles/{name}/model"),  # ProfileModelUpdate{provider, model}
    "profiles.description.put": ("PUT", "/api/profiles/{name}/description"),  # ProfileDescriptionUpdate{description}
    "config.raw.get": ("GET", "/api/config/raw?profile={name}"),
    "skills.list": ("GET", "/api/skills?profile={name}"),  # [{name, enabled, category, provenance, usage}]
    "skills.toggle": ("PUT", "/api/skills/toggle?profile={name}"),  # SkillToggle{name, enabled, profile}
    "toolsets.list": ("GET", "/api/tools/toolsets?profile={name}"),  # [{name, platform, enabled, available, tools}]
    "toolsets.toggle": ("PUT", "/api/tools/toolsets/{toolset}?profile={name}"),  # ToolsetToggle{enabled, profile}
    "mcp.list": ("GET", "/api/mcp/servers?profile={name}"),  # {"servers": [{name, transport, enabled, ...}]}
    "mcp.create": ("POST", "/api/mcp/servers?profile={name}"),  # MCPServerCreate{name, url|command, args, env, auth}
    "mcp.delete": ("DELETE", "/api/mcp/servers/{server}?profile={name}"),
    "messaging.platforms": ("GET", "/api/messaging/platforms?profile={name}"),
    "messaging.platform.put": ("PUT", "/api/messaging/platforms/{platform}?profile={name}"),  # not captured yet
    # Webhook routes are not profile-scoped: they act on the dashboard process's own home.
    "webhooks.list": ("GET", "/api/webhooks"),  # {enabled, base_url, subscriptions}
    "webhooks.create": ("POST", "/api/webhooks"),  # WebhookCreate{name, events, deliver, deliver_only, ...}
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
    # Services often run without ~/.local/bin on PATH, so the installer passes HERMES_BIN explicitly;
    # it also points HERMES_DASHBOARD_URL at the loopback dashboard it runs for the daemon.
    hermes_bin: str = field(default_factory=lambda: os.environ.get("HERMES_BIN") or shutil.which("hermes") or "hermes")
    dashboard_url: str = field(default_factory=lambda: os.environ.get("HERMES_DASHBOARD_URL", "http://127.0.0.1:9119"))
    dashboard_token: Optional[str] = field(default_factory=lambda: os.environ.get("HERMES_DASHBOARD_SESSION_TOKEN"))
    api_url: str = field(default_factory=lambda: os.environ.get("HERMES_API_URL", "http://127.0.0.1:8642"))
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
        headers = {DASHBOARD_SESSION_HEADER: self.cfg.dashboard_token} if self.cfg.dashboard_token else {}
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
            report["dashboard_auth_required"] = st.get("auth_required")
            if st.get("auth_required"):
                report["notes"].append("dashboard: auth gate is on (non-loopback bind); session-token writes may be refused")
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
        # Hooks and policy enforcement need the plugin enabled in config.yaml, not just copied in.
        plugin_dir = os.path.join(self.cfg.hermes_home, "plugins", "fleetcontrol")
        enabled = self._enabled_plugins()
        if not os.path.isdir(plugin_dir):
            report["plugins"]["fleetcontrol"] = "missing"
        elif "fleetcontrol" in enabled:
            report["plugins"]["fleetcontrol"] = "enabled"
        else:
            report["plugins"]["fleetcontrol"] = "installed, not enabled"
            report["notes"].append("fleetcontrol plugin: not in plugins.enabled; no evidence capture or policy "
                                   "enforcement until it is enabled and Hermes restarts")
        report["plugins"]["langfuse"] = "enabled" if "observability/langfuse" in enabled else "not enabled"
        # Capability ids consumed by blueprint.requires.capabilities
        caps_out = ["runs", "sessions", "profiles.read"] if report["surfaces"].get("api") == "ok" else []
        if report["surfaces"].get("dashboard") == "loopback" or report["surfaces"]["cli"] == "ok":
            caps_out += ["profiles.write"]
        if report["plugins"]["fleetcontrol"] == "enabled":
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

    def profiles(self) -> list[dict]:
        """Profiles on this instance. 0.21.2 wraps them as {"profiles": [...]}; a bare list is accepted too."""
        data = self.dashboard("profiles.list")
        if isinstance(data, dict):
            data = data.get("profiles")
        return [p for p in (data or []) if isinstance(p, dict)]

    def mcp_servers(self, name: str) -> list[str]:
        """Names of the enabled MCP servers on a profile. 0.21.2 returns {"servers": [{name, enabled, ...}]}."""
        data = self.dashboard("mcp.list", name=name)
        if isinstance(data, dict) and "servers" in data:
            data = data["servers"]
        if isinstance(data, dict):  # older shape: {server_name: config}
            return sorted(data)
        return sorted(m["name"] for m in (data or []) if isinstance(m, dict) and m.get("name") and m.get("enabled", True))

    def live_profile_state(self, name: str, profiles: Optional[list[dict]] = None) -> dict:
        """Live values of the managed fields for one profile, shaped like Blueprint.managed_fields().

        Pass ``profiles`` (from :meth:`profiles`) when scanning many profiles to avoid re-listing."""
        prof = next((p for p in (profiles if profiles is not None else self.profiles()) if p.get("name") == name), None)
        if prof is None:
            raise HermesLocalError(f"profile {name!r} not found on this instance")
        soul = self.dashboard("profiles.soul.get", name=name) or {}
        soul_text = (soul.get("content") or "").rstrip() + "\n"
        skills = [s["name"] for s in (self.dashboard("skills.list", name=name) or [])
                  if s.get("enabled") and s["name"] not in HERMES_ESSENTIAL_SKILLS]
        toolsets = {t["name"] for t in (self.dashboard("toolsets.list", name=name) or []) if t.get("enabled")}
        return {
            "description": prof.get("description"),
            "model": {"provider": prof.get("provider"), "name": prof.get("model")},
            "soul_sha256": "sha256:" + hashlib.sha256(soul_text.encode("utf-8")).hexdigest(),
            "skills": sorted(skills),
            "toolsets": sorted(toolsets),
            "mcps": self.mcp_servers(name),
        }

    # ------------------------------------------------------------------ writes (managed fields only)

    def ensure_profile(self, name: str, description: str, provider: str, model: str, clone_from: Optional[str] = None) -> None:
        existing = {p.get("name") for p in self.profiles()}
        if name not in existing:
            body = {"name": name, "description": description, "provider": provider, "model": model}
            if clone_from:
                body["clone_from"] = clone_from
            try:
                out = self.dashboard("profiles.create", body) or {}
            except HermesLocalError:
                # dashboard down or route changed: fall back to the CLI, same on-disk result
                args = ["profile", "create", name]
                if clone_from:
                    args += ["--clone-from", clone_from]
                self.cli(*args)
                return
            # The profile is created even when Hermes rejects the model (e.g. a provider without
            # credentials); it answers 200 with model_set=false instead of failing the request.
            if provider and model and out.get("model_set") is False:
                raise HermesLocalError(f"profile {name!r} created but model not set: {out.get('model_error') or 'no reason given'}")
        else:
            self.dashboard("profiles.description.put", {"description": description}, name=name)
            self.dashboard("profiles.model.put", {"provider": provider, "model": model}, name=name)

    def write_soul(self, name: str, content: str) -> None:
        self.dashboard("profiles.soul.put", {"content": content}, name=name)

    def set_skill(self, name: str, skill: str, enabled: bool) -> None:
        self.dashboard("skills.toggle", {"name": skill, "enabled": enabled, "profile": name}, name=name)

    def set_toolset(self, name: str, toolset: str, enabled: bool) -> None:
        self.dashboard("toolsets.toggle", {"enabled": enabled, "profile": name}, name=name, toolset=toolset)

    def sync_skills(self, name: str, wanted: list[str]) -> list[str]:
        """Enable exactly ``wanted`` on the profile. Returns the skills that were toggled."""
        live = {s["name"]: bool(s.get("enabled")) for s in (self.dashboard("skills.list", name=name) or [])
                if s["name"] not in HERMES_ESSENTIAL_SKILLS}
        wanted = [s for s in wanted if s not in HERMES_ESSENTIAL_SKILLS]
        return self._sync("skills", live, wanted, lambda s, on: self.set_skill(name, s, on))

    def sync_toolsets(self, name: str, wanted: list[str]) -> list[str]:
        """Enable exactly ``wanted`` on the profile. Returns the toolsets that were toggled."""
        live = {t["name"]: bool(t.get("enabled")) for t in (self.dashboard("toolsets.list", name=name) or [])}
        return self._sync("toolsets", live, wanted, lambda t, on: self.set_toolset(name, t, on))

    @staticmethod
    def _sync(kind: str, live: dict[str, bool], wanted: list[str], toggle: Callable[[str, bool], None]) -> list[str]:
        unknown = sorted(set(wanted) - set(live))
        if unknown:  # checked before any toggle, so a bad blueprint leaves the profile as it was
            raise HermesLocalError(f"{kind} not available on this instance: {', '.join(unknown)}")
        changed = [k for k, on in sorted(live.items()) if on != (k in wanted)]
        for k in changed:
            toggle(k, k in wanted)
        return changed

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
        if key == "skills":  # essential skills stay on whatever the blueprint says; they are not drift
            d, l = (None if x is None else sorted(set(x) - HERMES_ESSENTIAL_SKILLS) for x in (d, l))
        if d != l:
            out.append({"field": key, "blueprint": d, "live": l})
    return out
