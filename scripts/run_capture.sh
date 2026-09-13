#!/usr/bin/env bash
# Fleet Control — one-shot Hermes dashboard capture.
# Copy this file to the Hermes server and run:   bash run_capture.sh
# It: checks hermes+python, sets a dashboard token in ~/.hermes/.env, (re)starts the dashboard on
# loopback, verifies auth, runs the read-only capture and the write probe (creates+deletes a
# throwaway profile 'fleetcontrol-probe'), and leaves two files in your home directory:
#   ~/dashboard-capture.json   ~/dashboard-capture-write.json
# Nothing else on the server is changed. Re-runnable.
set -u
say(){ printf '\n== %s\n' "$*"; }
fail(){ printf '\n!! %s\n' "$*"; exit 1; }

say "1/7 checking prerequisites"
command -v hermes >/dev/null 2>&1 || fail "hermes CLI not found on PATH for user $(whoami). Run this as the user that runs Hermes."
command -v python3 >/dev/null 2>&1 || fail "python3 not found"
command -v curl >/dev/null 2>&1 || fail "curl not found"
echo "hermes: $(hermes --version 2>&1 | head -1)"; echo "python: $(python3 --version)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"; [ -d "$HERMES_HOME" ] || fail "$HERMES_HOME does not exist"
PORT="${HERMES_DASHBOARD_PORT:-9119}"

say "2/7 writing the capture script to ~/capture_dashboard_routes.py"
cat > "$HOME/capture_dashboard_routes.py" <<'PYEOF'
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
PYEOF

say "3/7 ensuring a known dashboard session token in $HERMES_HOME/.env"
touch "$HERMES_HOME/.env"; chmod 600 "$HERMES_HOME/.env"
if ! grep -q '^HERMES_DASHBOARD_SESSION_TOKEN=' "$HERMES_HOME/.env"; then
  echo "HERMES_DASHBOARD_SESSION_TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" >> "$HERMES_HOME/.env"
  echo "token created"
else
  echo "token already present"
fi
export HERMES_DASHBOARD_SESSION_TOKEN="$(grep '^HERMES_DASHBOARD_SESSION_TOKEN=' "$HERMES_HOME/.env" | tail -1 | cut -d= -f2- | tr -d "'\"")"

say "4/7 (re)starting the dashboard on 127.0.0.1:$PORT"
if pgrep -f "hermes dashboard" >/dev/null 2>&1; then
  echo "stopping existing dashboard (it may be using a different token)"; pkill -f "hermes dashboard"; sleep 2
fi
nohup hermes dashboard --port "$PORT" > "$HOME/dashboard.log" 2>&1 &
for i in $(seq 1 30); do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/status"; then break; fi; sleep 1
done
curl -s "http://127.0.0.1:$PORT/api/status" | head -c 300 || { echo; tail -30 "$HOME/dashboard.log"; fail "dashboard did not come up; log above"; }
echo

say "5/7 verifying the token is accepted"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "X-Hermes-Session: $HERMES_DASHBOARD_SESSION_TOKEN" "http://127.0.0.1:$PORT/api/profiles")
echo "GET /api/profiles -> HTTP $CODE"
if [ "$CODE" != "200" ]; then
  echo "trying legacy Authorization: Bearer header"
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $HERMES_DASHBOARD_SESSION_TOKEN" "http://127.0.0.1:$PORT/api/profiles")
  echo "-> HTTP $CODE"
fi
[ "$CODE" = "200" ] || { tail -30 "$HOME/dashboard.log"; fail "dashboard rejected the token (HTTP $CODE). Send me ~/dashboard.log"; }

say "6/7 read-only capture -> ~/dashboard-capture.json"
export HERMES_DASHBOARD_URL="http://127.0.0.1:$PORT"
python3 "$HOME/capture_dashboard_routes.py" > "$HOME/dashboard-capture.json"

say "7/7 write probe (throwaway profile fleetcontrol-probe) -> ~/dashboard-capture-write.json"
python3 "$HOME/capture_dashboard_routes.py" --write > "$HOME/dashboard-capture-write.json"
if hermes profile list 2>/dev/null | grep -q fleetcontrol-probe; then hermes profile delete fleetcontrol-probe --yes 2>/dev/null || true; fi

say "done. Send these two files back:"
ls -la "$HOME/dashboard-capture.json" "$HOME/dashboard-capture-write.json"
echo "hermes version: $(hermes --version 2>&1 | head -1)"
echo "(the dashboard is still running in the background; stop it with: pkill -f 'hermes dashboard')"
