#!/usr/bin/env python3
"""Hermes compatibility check — run nightly against the newest hermes-agent checkout.

It does NOT need Hermes installed or runnable. It reads the source tree and verifies the
things the Fleet Control Agent depends on are still there, so an upstream change surfaces as
a failing CI job with a named reason instead of a broken customer install.

Usage: python3 scripts/hermes_compat_check.py /path/to/hermes-agent [--json]
Exit 0 = compatible, 1 = incompatible (details printed), 2 = could not check.
"""

from __future__ import annotations

import json
import os
import re
import sys

CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn

    return deco


def read(root, rel):
    p = os.path.join(root, rel)
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# --- hooks the plugin registers ---------------------------------------------------------
PLUGIN_HOOKS = ["pre_tool_call", "post_tool_call", "subagent_start", "subagent_stop", "on_session_start", "on_session_end", "pre_approval_request", "post_approval_response"]


@check("plugin hook names still valid")
def _hooks(root):
    src = read(root, "hermes_cli/plugins.py") if os.path.exists(os.path.join(root, "hermes_cli/plugins.py")) else ""
    if "VALID_HOOKS" not in src:
        # fall back to the docs
        src = read(root, "website/docs/user-guide/features/hooks.md")
    missing = [h for h in PLUGIN_HOOKS if f'"{h}"' not in src and f"### `{h}`" not in src]
    return (not missing, f"missing hooks: {missing}" if missing else "ok")


@check("pre_tool_call still honours {'action': 'block'|'approve'}")
def _block(root):
    doc = read(root, "website/docs/user-guide/features/hooks.md")
    ok = '"action": "block"' in doc and '"action": "approve"' in doc
    return (ok, "ok" if ok else "block/approve contract not found in hooks.md")


@check("post_tool_call passes args and result")
def _post(root):
    src = read(root, "model_tools.py")
    ok = re.search(r'invoke_hook\(\s*"post_tool_call"[^)]*args=function_args[^)]*result=result', src, re.S) is not None
    return (ok, "ok" if ok else "post_tool_call call site changed in model_tools.py")


# --- dashboard routes the daemon calls ----------------------------------------------------
DASHBOARD_ROUTES = {
    "hermes_cli/web_routers/profiles.py": ['"/api/profiles"', '"/api/profiles/{name}/soul"', '"/api/profiles/{name}/model"', '"/api/profiles/{name}/description"'],
    "hermes_cli/web_routers/skills.py": ['"/api/skills"', '"/api/skills/toggle"'],
    "hermes_cli/web_routers/tools.py": ['"/api/tools/toolsets"'],
    "hermes_cli/web_routers/mcp.py": ['"/api/mcp/servers"'],
    "hermes_cli/web_routers/messaging.py": ['"/api/messaging/platforms"'],
    "hermes_cli/web_routers/ops.py": ['"/api/webhooks"'],
    "hermes_cli/web_routers/status.py": ['"/api/status"'],
}


@check("dashboard routes used by fleetctl-agent still exist")
def _routes(root):
    missing = []
    for rel, needles in DASHBOARD_ROUTES.items():
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            missing.append(f"{rel} (file gone)")
            continue
        src = read(root, rel)
        for n in needles:
            # routes are declared as @router.get("/api/...") or with the prefix split; accept either
            bare = n.strip('"').replace("/api", "")
            if n not in src and f'"{bare}"' not in src:
                missing.append(f"{rel}: {n}")
    return (not missing, f"missing: {missing}" if missing else "ok")


def _daemon_header():
    """The session header fleetctl-agent sends, read from its source so the two cannot diverge."""
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "apps", "agent", "fleetctl_agent", "hermes_local.py"), encoding="utf-8") as f:
        m = re.search(r'DASHBOARD_SESSION_HEADER\s*=\s*"([^"]+)"', f.read())
    return m.group(1) if m else "X-Hermes-Session-Token"


@check("dashboard session header matches the one fleetctl-agent sends")
def _hdr(root):
    src = read(root, "hermes_cli/web_server.py")
    want = _daemon_header()
    m = re.search(r'_SESSION_HEADER_NAME\s*=\s*"([^"]+)"', src)
    if m:
        ok = m.group(1) == want
        return (ok, "ok" if ok else f"dashboard expects {m.group(1)}, fleetctl-agent sends {want}")
    # No constant: require the exact quoted header, not a prefix of it.
    ok = f'"{want}"' in src
    return (ok, "ok" if ok else f"{want} not found in web_server.py")


def _daemon_essential_skills():
    """The skills fleetctl-agent leaves unmanaged, read from its source so the two cannot diverge."""
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "apps", "agent", "fleetctl_agent", "hermes_local.py"), encoding="utf-8") as f:
        m = re.search(r"HERMES_ESSENTIAL_SKILLS\s*=\s*frozenset\(\{([^}]*)\}\)", f.read())
    return set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()


@check("essential skills match the ones fleetctl-agent leaves unmanaged")
def _essential(root):
    rel = "agent/skill_utils.py"
    if not os.path.exists(os.path.join(root, rel)):
        return (False, f"{rel} gone; find where Hermes pins always-on skills")
    m = re.search(r"ESSENTIAL_SKILLS\s*(?::[^=\n]*)?=\s*frozenset\(\{([^}]*)\}\)", read(root, rel))
    if not m:
        return (False, f"ESSENTIAL_SKILLS not found in {rel}")
    upstream, ours = set(re.findall(r'"([^"]+)"', m.group(1))), _daemon_essential_skills()
    ok = upstream == ours
    return (ok, "ok" if ok else f"Hermes pins {sorted(upstream)}, fleetctl-agent leaves {sorted(ours)} unmanaged")


# --- request bodies the daemon sends (hermes_cli/web_models.py) ---------------------------
REQUEST_BODIES = {
    "ProfileCreate": ["name", "clone_from", "description", "provider", "model"],
    "ProfileSoulUpdate": ["content"],
    "ProfileDescriptionUpdate": ["description"],
    "ProfileModelUpdate": ["provider", "model"],
    "SkillToggle": ["name", "enabled", "profile"],
    "ToolsetToggle": ["enabled", "profile"],
}


@check("dashboard request bodies still accept the fields fleetctl-agent sends")
def _bodies(root):
    src = read(root, "hermes_cli/web_models.py")
    missing = []
    for cls, fields in REQUEST_BODIES.items():
        m = re.search(rf"^class {cls}\([^\n]*\n((?:[ \t]+[^\n]*\n|[ \t]*\n)*)", src, re.M)
        if not m:
            missing.append(f"{cls} (class gone)")
            continue
        missing += [f"{cls}.{f}" for f in fields if not re.search(rf"^[ \t]+{re.escape(f)}[ \t]*:", m.group(1), re.M)]
    return (not missing, f"missing: {missing}" if missing else "ok")


@check("/v1 API server routes used still exist")
def _api(root):
    src = read(root, "gateway/platforms/api_server.py") + read(root, "gateway/platforms/api_server_runs.py")
    needles = ['"/health"', '"/v1/capabilities"', '"/v1/runs"', '"/v1/runs/{run_id}/events"', '"/api/sessions/{session_id}/messages"']
    missing = [n for n in needles if n not in src]
    return (not missing, f"missing: {missing}" if missing else "ok")


@check("session messages still expose tool_calls (after-the-fact evidence)")
def _msgs(root):
    src = read(root, "gateway/platforms/api_server.py")
    ok = '"tool_calls"' in src and '"tool_call_id"' in src
    return (ok, "ok" if ok else "_message_response no longer includes tool_calls")


@check("outbound webhooks still supported (API-only mode)")
def _outbound(root):
    doc = read(root, "website/docs/user-guide/features/hooks.md")
    ok = "hooks.outbound" in doc or "outbound:" in doc
    return (ok, "ok" if ok else "outbound webhooks removed from docs")


@check("plugin discovery directory unchanged (~/.hermes/plugins)")
def _plugdir(root):
    doc = read(root, "website/docs/user-guide/features/plugins.md")
    ok = "~/.hermes/plugins/" in doc
    return (ok, "ok" if ok else "plugin directory convention changed")


@check("Hermes version readable")
def _version(root):
    src = read(root, "hermes_cli/__init__.py")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', src)
    return (m is not None, f"hermes-agent {m.group(1)}" if m else "no __version__")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = argv[1]
    as_json = "--json" in argv
    if not os.path.isdir(os.path.join(root, "hermes_cli")):
        print(f"not a hermes-agent checkout: {root}")
        return 2
    results = []
    for name, fn in CHECKS:
        try:
            ok, detail = fn(root)
        except Exception as exc:
            ok, detail = False, f"check crashed: {exc}"
        results.append({"check": name, "ok": ok, "detail": detail})
    if as_json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print(("PASS " if r["ok"] else "FAIL ") + r["check"] + " — " + r["detail"])
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
