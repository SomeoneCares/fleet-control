#!/usr/bin/env sh
# Runs every test suite with the stdlib runner (no pytest needed). CI and developers use the same command.
# PYTHON picks the interpreter, e.g. PYTHON=.venv/Scripts/python on Windows, where python3 is a Store stub.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
case "$PY" in /*|?:*) ;; */*) PY="$(pwd)/$PY" ;; esac  # relative path: pin it before cd'ing into each package
export PYTHONPATH="$ROOT/packages/blueprint_schema:$ROOT/packages/hermes_plugin:$ROOT/apps/agent:$ROOT/apps/api:$ROOT/scripts"
for d in packages/blueprint_schema packages/hermes_plugin apps/agent apps/api scripts; do
  echo "== $d"; (cd "$ROOT/$d" && "$PY" -m unittest discover -s tests -t . -q)
done
