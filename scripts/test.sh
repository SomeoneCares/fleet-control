#!/usr/bin/env sh
# Runs every test suite with the stdlib runner (no pytest needed). CI and developers use the same command.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT/packages/blueprint_schema:$ROOT/packages/hermes_plugin:$ROOT/apps/agent:$ROOT/apps/api:$ROOT/scripts"
for d in packages/blueprint_schema packages/hermes_plugin apps/agent apps/api scripts; do
  echo "== $d"; (cd "$ROOT/$d" && python3 -m unittest discover -s tests -t . -q)
done
