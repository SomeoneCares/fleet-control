#!/usr/bin/env sh
# Fleet Control Agent installer (Linux/macOS). Run on the Hermes host as the user that runs Hermes.
#   FLEETCONTROL_URL=https://fleetcontrol.example FLEETCONTROL_INSTANCE_ID=hermes-prod-eu-01 \
#   FLEETCONTROL_PAIRING_TOKEN=pair_... sh install-agent.sh
# What it does: installs fleetctl-agent into its own venv, copies the fleetcontrol plugin into
# ~/.hermes/plugins/fleetcontrol (not enabled yet; see the last line), starts a Hermes dashboard of its
# own on 127.0.0.1:$FLEETCONTROL_DASHBOARD_PORT (default 9129) with a session token only the daemon
# knows, pairs with Fleet Control, and installs systemd (Linux) or launchd (macOS) services.
# Nothing is exposed on the network. A dashboard you already run, and ~/.hermes/.env, are not touched.
# Re-runnable: the token and the pairing are kept.
set -eu
: "${FLEETCONTROL_URL:?set FLEETCONTROL_URL}"
: "${FLEETCONTROL_INSTANCE_ID:?set FLEETCONTROL_INSTANCE_ID}"
: "${FLEETCONTROL_PAIRING_TOKEN:?set FLEETCONTROL_PAIRING_TOKEN}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
STATE="${FLEETCONTROL_STATE_DIR:-$HOME/.fleetctl-agent}"
DPORT="${FLEETCONTROL_DASHBOARD_PORT:-9129}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"

# Non-login shells and services often lack ~/.local/bin on PATH; fall back to the usual install locations.
HB="${HERMES_BIN:-$(command -v hermes 2>/dev/null || true)}"
for c in "$HOME/.local/bin/hermes" "$HERMES_HOME/hermes-agent/venv/bin/hermes"; do
  if [ -z "$HB" ] && [ -x "$c" ]; then HB="$c"; fi
done
[ -n "$HB" ] || { echo "hermes CLI not found; set HERMES_BIN"; exit 1; }
python3 - <<'PY' || { echo "python >= 3.11 required"; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY

mkdir -p "$STATE" "$HERMES_HOME/plugins"
umask 077
python3 -m venv "$STATE/venv"
"$STATE/venv/bin/pip" install -q --upgrade pip
"$STATE/venv/bin/pip" install -q "$SRC/apps/agent"

# Plugin: plain files under ~/.hermes/plugins/<name>/ (hermes plugin loader convention)
rm -rf "$HERMES_HOME/plugins/fleetcontrol"
cp -R "$SRC/packages/hermes_plugin/fleetcontrol" "$HERMES_HOME/plugins/fleetcontrol"

# The daemon's own loopback dashboard. Hermes reads HERMES_DASHBOARD_SESSION_TOKEN at start and accepts
# it as X-Hermes-Session-Token; a loopback bind keeps its auth gate off, so writes work.
TOKEN="$(sed -n 's/^HERMES_DASHBOARD_SESSION_TOKEN=//p' "$STATE/dashboard.env" 2>/dev/null | tail -1)"
[ -n "$TOKEN" ] || TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
cat > "$STATE/dashboard.env" <<ENV
HERMES_HOME=$HERMES_HOME
HERMES_DASHBOARD_SESSION_TOKEN=$TOKEN
ENV

cat > "$STATE/env" <<ENV
FLEETCONTROL_URL=$FLEETCONTROL_URL
FLEETCONTROL_INSTANCE_ID=$FLEETCONTROL_INSTANCE_ID
FLEETCONTROL_PAIRING_TOKEN=$FLEETCONTROL_PAIRING_TOKEN
FLEETCONTROL_STATE_DIR=$STATE
HERMES_HOME=$HERMES_HOME
HERMES_BIN=$HB
HERMES_DASHBOARD_URL=http://127.0.0.1:$DPORT
HERMES_DASHBOARD_SESSION_TOKEN=$TOKEN
ENV
# The Hermes API server key, when the API server is on, lets the daemon read /v1/capabilities.
grep '^API_SERVER_KEY=' "$HERMES_HOME/.env" 2>/dev/null | tail -1 >> "$STATE/env" || true

wait_for_dashboard() {
  for _ in $(seq 1 60); do
    python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:$DPORT/api/status',timeout=2)" 2>/dev/null && return 0
    sleep 2
  done
  echo "the loopback dashboard on 127.0.0.1:$DPORT did not come up"; return 1
}

if [ "$(uname)" = "Linux" ] && command -v systemctl >/dev/null 2>&1; then
  UNITS="$HOME/.config/systemd/user"
  mkdir -p "$UNITS"
  cat > "$UNITS/fleetctl-dashboard.service" <<UNIT
[Unit]
Description=Hermes dashboard on loopback for the Fleet Control Agent
[Service]
EnvironmentFile=$STATE/dashboard.env
ExecStart=$HB dashboard --host 127.0.0.1 --port $DPORT --no-open --skip-build
Restart=always
RestartSec=5
[Install]
WantedBy=default.target
UNIT
  cat > "$UNITS/fleetctl-agent.service" <<UNIT
[Unit]
Description=Fleet Control Agent for Hermes
After=network-online.target fleetctl-dashboard.service
Wants=fleetctl-dashboard.service
[Service]
EnvironmentFile=$STATE/env
ExecStart=$STATE/venv/bin/fleetctl-agent
Restart=always
RestartSec=5
[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable fleetctl-dashboard fleetctl-agent >/dev/null
  systemctl --user restart fleetctl-dashboard
  wait_for_dashboard
  systemctl --user restart fleetctl-agent
  echo "fleetctl-agent installed and started (systemd --user). Logs: journalctl --user -u fleetctl-agent -f"
elif [ "$(uname)" = "Darwin" ]; then
  plist() {  # label, env file, program arguments as <string> elements
    cat > "$HOME/Library/LaunchAgents/$1.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$1</string>
<key>ProgramArguments</key><array>$3</array>
<key>EnvironmentVariables</key><dict>$(sed 's/^\([^=]*\)=\(.*\)$/<key>\1<\/key><string>\2<\/string>/' "$2")</dict>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
</dict></plist>
PL
    launchctl unload "$HOME/Library/LaunchAgents/$1.plist" 2>/dev/null || true
    launchctl load "$HOME/Library/LaunchAgents/$1.plist"
  }
  mkdir -p "$HOME/Library/LaunchAgents"
  plist io.fleetcontrol.dashboard "$STATE/dashboard.env" \
    "<string>$HB</string><string>dashboard</string><string>--host</string><string>127.0.0.1</string><string>--port</string><string>$DPORT</string><string>--no-open</string><string>--skip-build</string>"
  wait_for_dashboard
  plist io.fleetcontrol.agent "$STATE/env" "<string>$STATE/venv/bin/fleetctl-agent</string>"
  echo "fleetctl-agent installed and started (launchd)."
else
  echo "No service manager found; run manually:"
  echo "  set -a; . $STATE/dashboard.env; $HB dashboard --host 127.0.0.1 --port $DPORT --no-open --skip-build &"
  echo "  set -a; . $STATE/env; $STATE/venv/bin/fleetctl-agent"
fi
echo "The fleetcontrol plugin is copied but not enabled. To capture evidence and enforce policies, add it to"
echo "plugins.enabled in $HERMES_HOME/config.yaml and restart Hermes (hermes gateway restart)."
