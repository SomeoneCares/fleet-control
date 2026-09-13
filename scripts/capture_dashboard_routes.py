#!/usr/bin/env python3
"""Capture real request/response pairs for the Hermes dashboard routes fleetctl-agent uses.

Run on a host with Hermes installed, while `hermes dashboard` is running on loopback:

    HERMES_DASHBOARD_SESSION_TOKEN=<token> python3 scripts/capture_dashboard_routes.py > dashboard-capture.json

The token is whatever the dashboard printed at startup, or the value you put in ~/.hermes/.env as
HERMES_DASHBOARD_SESSION_TOKEN. Nothing is written to Hermes unless you pass --write, which creates
and then deletes a throwaway profile named `fleetcontrol-probe`. Secrets in responses are masked.

Paste the resulting JSON back to the Fleet Control repo as docs/dashboard-capture-<hermes-version>.json.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("HERMES_DASHBOARD_URL", "http://127.0.0.1:9119")
TOKEN = os.environ.get("HERMES_DASHBOARD_SESSION_TOKEN", "")
WRITE = "--write" in sys.argv
PROBE = "fleetcontrol-probe"

READS = [
    ("GET", "/api/status"),
    ("GET", "/api/profiles"),
    ("GET", "/api/profiles/default/soul"),
    ("GET", "/api/config/raw?profile=default"),
    ("GET", "/api/config?profile=default"),
    ("GET", "/api/skills?profile=default"),
    ("GET", "/api/tools/toolsets?profile=default"),
    ("GET", "/api/mcp/servers?profile=default"),
    ("GET", "/api/messaging/platforms?profile=default"),
    ("GET", "/api/cron/jobs?profile=all"),
    ("GET", "/api/webhooks?profile=default"),
]

WRITES = [
    ("POST", "/api/profiles", {"name": PROBE, "description": "Fleet Control probe (safe to delete)", "provider": "openai", "model": "gpt-4o-mini"}),
    ("GET", f"/api/profiles/{PROBE}/soul", None),
    ("PUT", f"/api/profiles/{PROBE}/soul", {"content": "# Objective\n\nProbe profile created by Fleet Control capture script.\n"}),
    ("GET", f"/api/profiles/{PROBE}/soul", None),
    ("PUT", f"/api/profiles/{PROBE}/description", {"description": "Fleet Control probe, updated"}),
    ("GET", f"/api/skills?profile={PROBE}", None),
    ("GET", f"/api/tools/toolsets?profile={PROBE}", None),
    ("GET", f"/api/mcp/servers?profile={PROBE}", None),
    ("DELETE", f"/api/profiles/{PROBE}", None),
    ("GET", "/api/profiles", None),
]

_MASK = re.compile(r'("?(?:api_key|token|secret|password|bearer_token|redacted_value)"?\s*[:=]\s*")([^"]{4})[^"]*(")', re.I)


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("X-Hermes-Session", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode("utf-8", "replace"), e.code
    except Exception as e:
        return {"method": method, "path": path, "request": body, "error": str(e)}
    raw = _MASK.sub(r"\1\2…[masked]\3", raw)
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = raw[:4000]
    return {"method": method, "path": path, "request": body, "status": status, "response": parsed}


def main():
    out = {"base": BASE, "token_present": bool(TOKEN), "write_probe": WRITE, "calls": []}
    for m, p in READS:
        out["calls"].append(call(m, p))
    if WRITE:
        for m, p, b in WRITES:
            out["calls"].append(call(m, p, b))
    json.dump(out, sys.stdout, indent=2)
    print(file=sys.stderr)
    bad = [c for c in out["calls"] if c.get("error") or c.get("status", 0) >= 400]
    print(f"{len(out['calls'])} calls, {len(bad)} failed", file=sys.stderr)
    for c in bad:
        print(f"  {c['method']} {c['path']} -> {c.get('status') or c.get('error')}", file=sys.stderr)


if __name__ == "__main__":
    main()
