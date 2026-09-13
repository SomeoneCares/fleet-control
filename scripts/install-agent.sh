#!/usr/bin/env sh
# Fleet Control Agent installer (Linux/macOS). Run on the Hermes host as the user that runs Hermes.
#   FLEETCONTROL_URL=https://fleetcontrol.example FLEETCONTROL_INSTANCE_ID=hermes-prod-eu-01 \
#   FLEETCONTROL_PAIRING_TOKEN=pair_... sh install-agent.sh
# What it does: installs fleetctl-agent into its own venv, copies the fleetcontrol plugin into
# ~/.hermes/plugins/fleetcontrol, enables it in config.yaml if needed, sets a dashboard session
# token so the daemon can call the loopback dashboard, pairs with Fleet Control, and installs a
# systemd (Linux) or launchd (macOS) service. Nothing is exposed on the network.
set -eu
: "${FLEETCONTROL_URL:?set FLEETCONTROL_URL}"
: "${FLEETCONTROL_INSTANCE_ID:?set FLEETCONTROL_INSTANCE_ID}"
: "${FLEETCONTROL_PAIRING_TOKEN:?set FLEETCONTROL_PAIRING_TOKEN}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
STATE="${FLEETCONTROL_STATE_DIR:-$HOME/.fleetctl-agent}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"

command -v hermes >/dev/null 2>&1 || { echo "hermes CLI not found on PATH"; exit 1; }
python3 - <<'PY' || { echo "python >= 3.11 required"; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY

mkdir -p "$STATE" "$HERMES_HOME/plugins" "$HERMES_HOME/fleetcontrol"
python3 -m venv "$STATE/venv"
"$STATE/venv/bin/pip" install -q --upgrade pip
"$STATE/venv/bin/pip" install -q "$SRC/apps/agent"

# Plugin: plain files under ~/.hermes/plugins/<name>/ (hermes plugin loader convention)
rm -rf "$HERMES_HOME/plugins/fleetcontrol"
cp -R "$SRC/packages/hermes_plugin/fleetcontrol" "$HERMES_HOME/plugins/fleetcontrol"

# Dashboard session token the daemon will present on loopback; Hermes reads it from .env
if ! grep -q '^HERMES_DASHBOARD_SESSION_TOKEN=' "$HERMES_HOME/.env" 2>/dev/null; then
  TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
  printf 'HERMES_DASHBOARD_SESSION_TOKEN=%s\n' "$TOKEN" >> "$HERMES_HOME/.env"
  chmod 600 "$HERMES_HOME/.env"
fi
DASH_TOKEN="$(sed -n 's/^HERMES_DASHBOARD_SESSION_TOKEN=//p' "$HERMES_HOME/.env" | tail -1)"

cat > "$STATE/env" <<ENV
FLEETCONTROL_URL=$FLEETCONTROL_URL
FLEETCONTROL_INSTANCE_ID=$FLEETCONTROL_INSTANCE_ID
FLEETCONTROL_PAIRING_TOKEN=$FLEETCONTROL_PAIRING_TOKEN
FLEETCONTROL_STATE_DIR=$STATE
HERMES_HOME=$HERMES_HOME
HERMES_DASHBOARD_SESSION_TOKEN=$DASH_TOKEN
ENV
chmod 600 "$STATE/env"

if [ "$(uname)" = "Linux" ] && command -v systemctl >/dev/null 2>&1; then
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/fleetctl-agent.service" <<UNIT
[Unit]
Description=Fleet Control Agent for Hermes
After=network-online.target
[Service]
EnvironmentFile=$STATE/env
ExecStart=$STATE/venv/bin/fleetctl-agent
Restart=always
RestartSec=5
[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload && systemctl --user enable --now fleetctl-agent
  echo "fleetctl-agent installed and started (systemd --user). Logs: journalctl --user -u fleetctl-agent -f"
elif [ "$(uname)" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/io.fleetcontrol.agent.plist"
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>io.fleetcontrol.agent</string>
<key>ProgramArguments</key><array><string>$STATE/venv/bin/fleetctl-agent</string></array>
<key>EnvironmentVariables</key><dict>$(sed 's/\(.*\)=\(.*\)/<key>\1<\/key><string>\2<\/string>/' "$STATE/env")</dict>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
</dict></plist>
PL
  launchctl unload "$PLIST" 2>/dev/null || true; launchctl load "$PLIST"
  echo "fleetctl-agent installed and started (launchd)."
else
  echo "No service manager found; run manually: set -a; . $STATE/env; $STATE/venv/bin/fleetctl-agent"
fi
echo "Restart Hermes (hermes gateway restart) so the fleetcontrol plugin loads."
