#!/usr/bin/env bash
# Fleet Control — one-shot Hermes dashboard capture.
# Copy this file to the Hermes server and run:   bash run_capture.sh
# It starts a TEMPORARY dashboard on 127.0.0.1:$CAPTURE_PORT (default 9129) with a one-off session token,
# runs the read-only capture and the write probe (creates+deletes a throwaway profile 'fleetcontrol-probe'),
# stops the temporary dashboard, and leaves two files (mode 600, secrets masked) in your home directory:
#   ~/dashboard-capture.json   ~/dashboard-capture-write.json
# A dashboard that is already running (systemd, LAN-bound, password-protected) is not touched, and
# nothing is written to ~/.hermes/.env. Re-runnable.
# The embedded Python below must stay identical to scripts/capture_dashboard_routes.py (scripts/tests checks it).
set -u
say(){ printf '\n== %s\n' "$*"; }
fail(){ printf '\n!! %s\n' "$*"; exit 1; }

say "1/6 checking prerequisites"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"; [ -d "$HERMES_HOME" ] || fail "$HERMES_HOME does not exist"
# Non-login SSH shells often lack ~/.local/bin on PATH; fall back to the usual install locations.
HB="$(command -v hermes 2>/dev/null || true)"
for c in "$HOME/.local/bin/hermes" "$HERMES_HOME/hermes-agent/venv/bin/hermes"; do
  if [ -z "$HB" ] && [ -x "$c" ]; then HB="$c"; fi
done
[ -n "$HB" ] || fail "hermes CLI not found for user $(whoami). Run this as the user that runs Hermes."
command -v python3 >/dev/null 2>&1 || fail "python3 not found"
command -v curl >/dev/null 2>&1 || fail "curl not found"
echo "hermes: $("$HB" --version 2>&1 | head -1)"; echo "python: $(python3 --version)"
PORT="${CAPTURE_PORT:-9129}"
if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/status"; then fail "port $PORT is already in use; set CAPTURE_PORT to a free port"; fi

say "2/6 writing the capture script to ~/capture_dashboard_routes.py"
cat > "$HOME/capture_dashboard_routes.py" <<'PYEOF'
#!/usr/bin/env python3
"""Capture real request/response pairs for the Hermes dashboard routes fleetctl-agent uses.

Run on a host with Hermes installed, against a dashboard bound to loopback:

    HERMES_DASHBOARD_SESSION_TOKEN=<token> python3 scripts/capture_dashboard_routes.py > dashboard-capture.json

The token is the HERMES_DASHBOARD_SESSION_TOKEN the dashboard was started with; it is sent as
X-Hermes-Session-Token (hermes-agent 0.21.2). scripts/run_capture.sh starts a temporary loopback
dashboard with a fresh token and runs this for you. Nothing is written to Hermes unless you pass
--write, which creates and then deletes a throwaway profile named `fleetcontrol-probe`.
Secrets in responses are masked: JSON fields, YAML/.env lines inside config/raw, Telegram bot
tokens, and the 4-character prefix Hermes leaves on redacted values.

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
HEADER = os.environ.get("HERMES_DASHBOARD_HEADER", "X-Hermes-Session-Token")
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

_SENSITIVE = r"[A-Za-z_]*(?:api_?key|_key|token|secret|password)[A-Za-z_]*"
_MASKS = [
    # JSON string fields: "api_key": "...", "password_hash": "...", "bot_token": "..." (empty = unset, kept)
    (re.compile(r'("' + _SENSITIVE + r'"\s*:\s*")[^"]+(")', re.I), r"\1***masked***\2"),
    # Hermes's own redaction keeps a 4-character prefix ("KMXD…[masked]"); drop it.
    (re.compile(r'("redacted_value"\s*:\s*")[^"]+(")'), r"\1***\2"),
    # YAML/.env lines inside JSON strings (config/raw returns config.yaml verbatim, as "...\n...").
    # Numbers, booleans and null stay readable. The value must start with a non-space so the
    # separator's spaces cannot be handed back to it (which would dodge the exclusion).
    (re.compile(r"((?:^|\\n)[ \t]*" + _SENSITIVE + r"[ \t]*[:=][ \t]*)"
                r"(?!(?:-?\d+(?:\.\d+)?|true|false|null|\*\*\*masked\*\*\*)[ \t]*(?:\\n|\"|$))[^\s\\\"][^\\\"\n]*", re.I),
     r"\1***masked***"),
    # Telegram bot tokens wherever they appear.
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"), "***masked***"),
]


def mask(raw):
    for pattern, repl in _MASKS:
        raw = pattern.sub(repl, raw)
    return raw


def _auth_value(header):
    return "Bearer " + TOKEN if header.lower() == "authorization" else TOKEN


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header(HEADER, _auth_value(HEADER))
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode("utf-8", "replace"), e.code
    except Exception as e:
        return {"method": method, "path": path, "request": body, "error": str(e)}
    raw = mask(raw)
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = raw[:4000]
    return {"method": method, "path": path, "request": body, "status": status, "response": parsed}


def probe_auth():
    """Which auth headers the dashboard accepts for GET /api/profiles (status per header)."""
    out = {}
    for header in ("X-Hermes-Session-Token", "Authorization", "X-Hermes-Session", "none"):
        req = urllib.request.Request(BASE + "/api/profiles", method="GET")
        if header != "none":
            req.add_header(header, _auth_value(header))
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                out[header] = r.status
        except urllib.error.HTTPError as e:
            out[header] = e.code
        except Exception as e:
            out[header] = str(e)
    return out


def main():
    out = {"base": BASE, "token_present": bool(TOKEN), "header": HEADER, "write_probe": WRITE,
           "auth_probe": probe_auth() if TOKEN else None, "calls": []}
    for m, p in READS:
        out["calls"].append(call(m, p))
    if WRITE:
        for m, p, b in WRITES:
            out["calls"].append(call(m, p, b))
    json.dump(out, sys.stdout, indent=2)
    print(file=sys.stderr)
    bad = [c for c in out["calls"] if c.get("error") or c.get("status", 0) >= 400]
    print(f"{len(out['calls'])} calls, {len(bad)} failed; auth probe: {out['auth_probe']}", file=sys.stderr)
    for c in bad:
        print(f"  {c['method']} {c['path']} -> {c.get('status') or c.get('error')}", file=sys.stderr)


if __name__ == "__main__":
    main()
PYEOF

say "3/6 starting a temporary dashboard on 127.0.0.1:$PORT"
TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
LOG="$(mktemp)"
if command -v setsid >/dev/null 2>&1; then
  HERMES_DASHBOARD_SESSION_TOKEN="$TOKEN" setsid nohup "$HB" dashboard --host 127.0.0.1 --port "$PORT" --no-open --skip-build > "$LOG" 2>&1 &
else
  HERMES_DASHBOARD_SESSION_TOKEN="$TOKEN" nohup "$HB" dashboard --host 127.0.0.1 --port "$PORT" --no-open --skip-build > "$LOG" 2>&1 &
fi
DPID=$!
cleanup(){ kill -TERM -- "-$DPID" 2>/dev/null || kill -TERM "$DPID" 2>/dev/null; sleep 2; kill -KILL -- "-$DPID" 2>/dev/null; rm -f "$LOG"; }
trap cleanup EXIT
for i in $(seq 1 60); do curl -s -o /dev/null "http://127.0.0.1:$PORT/api/status" && break; sleep 2; done
STATUS="$(curl -s "http://127.0.0.1:$PORT/api/status" || true)"
[ -n "$STATUS" ] || { tail -30 "$LOG" | sed "s/$TOKEN/<token>/g"; fail "temporary dashboard did not come up; log above"; }
echo "$STATUS" | head -c 300; echo

say "4/6 verifying the session token"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "X-Hermes-Session-Token: $TOKEN" "http://127.0.0.1:$PORT/api/profiles")
echo "GET /api/profiles with X-Hermes-Session-Token -> HTTP $CODE"
[ "$CODE" = "200" ] || fail "the temporary dashboard rejected its own session token (HTTP $CODE)"

say "5/6 read-only capture -> ~/dashboard-capture.json"
export HERMES_DASHBOARD_URL="http://127.0.0.1:$PORT" HERMES_DASHBOARD_SESSION_TOKEN="$TOKEN"
umask 077
python3 "$HOME/capture_dashboard_routes.py" > "$HOME/dashboard-capture.json"

say "6/6 write probe (throwaway profile fleetcontrol-probe) -> ~/dashboard-capture-write.json"
python3 "$HOME/capture_dashboard_routes.py" --write > "$HOME/dashboard-capture-write.json"
if "$HB" profile list 2>/dev/null | grep -q fleetcontrol-probe; then "$HB" profile delete fleetcontrol-probe --yes 2>/dev/null || true; fi

say "done. Send these two files back:"
ls -la "$HOME/dashboard-capture.json" "$HOME/dashboard-capture-write.json"
echo "hermes version: $("$HB" --version 2>&1 | head -1)"
echo "(the temporary dashboard is stopped on exit; any dashboard you already run was not touched)"
