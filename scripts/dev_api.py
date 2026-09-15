#!/usr/bin/env python3
"""Run the Fleet Control API for local development, with a bootstrap admin.

    python scripts/dev_api.py          # http://127.0.0.1:8080  (.venv/Scripts/python on Windows)
    python scripts/dev_api.py --fresh  # start over with an empty database

Data persists in .fleetcontrol-dev.db (SQLite, git-ignored) at the repo root, so people, instances, agent
pairings, blueprints, plans and the audit log survive a restart. Set FLEETCONTROL_DATABASE_URL to use
another database (e.g. a local PostgreSQL). The first run writes .fleetcontrol-dev-credentials.json
(git-ignored) with the admin's email and a random password; later runs reuse it. scripts/dev_seed.py
signs in with these credentials, creates demo people for each role and adds their passwords to the file.
"""

from __future__ import annotations

import json
import os
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDENTIALS = os.path.join(ROOT, ".fleetcontrol-dev-credentials.json")
DATABASE = os.path.join(ROOT, ".fleetcontrol-dev.db")
ADMIN_EMAIL = "admin@fleetcontrol.local"


def load_credentials() -> dict:
    if os.path.exists(CREDENTIALS):
        with open(CREDENTIALS, encoding="utf-8") as f:
            return json.load(f)
    creds = {"admin": {"email": ADMIN_EMAIL, "password": secrets.token_urlsafe(12)}, "people": {}}
    save_credentials(creds)
    return creds


def save_credentials(creds: dict) -> None:
    with open(CREDENTIALS, "w", encoding="utf-8") as f:
        json.dump(creds, f, indent=2)
    try:
        os.chmod(CREDENTIALS, 0o600)
    except OSError:
        pass


def main() -> None:
    if "--fresh" in sys.argv[1:] and os.path.exists(DATABASE):
        os.remove(DATABASE)
        print(f"Fleet Control API (dev): removed {DATABASE}; starting with an empty database", flush=True)
    creds = load_credentials()
    os.environ["FLEETCONTROL_ADMIN_EMAIL"] = creds["admin"]["email"]  # used only while the database has nobody
    os.environ["FLEETCONTROL_ADMIN_PASSWORD"] = creds["admin"]["password"]
    os.environ.setdefault("FLEETCONTROL_DATABASE_URL", "sqlite:///" + DATABASE.replace(os.sep, "/"))
    sys.path[:0] = [os.path.join(ROOT, "packages", "blueprint_schema"), os.path.join(ROOT, "apps", "api")]
    print(f"Fleet Control API (dev): sign in as {creds['admin']['email']}; the password is in {CREDENTIALS}", flush=True)
    import uvicorn

    uvicorn.run("fleetcontrol_api.main:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
