"""Signed-in TestClients for the API tests. Import only from modules already guarded on FastAPI/httpx.

Sessions are cookies; every write also carries the X-Fleet-Control header the web client sends.
"""

import os

from fastapi.testclient import TestClient

from fleetcontrol_api.auth import hash_password
from fleetcontrol_api.main import app, store

PASSWORD = "test-only password 42"
_HASH = hash_password(PASSWORD)


def user(role: str = "admin", email: str | None = None) -> str:
    email = email or f"{role.replace('_', '-')}-{os.urandom(3).hex()}@test.local"
    if not store.get_user(email):
        store.add_user(email, email.split("@")[0], role, _HASH)
    return email


def signed_in(role: str = "admin", email: str | None = None) -> TestClient:
    email = user(role, email)
    c = TestClient(app, headers={"X-Fleet-Control": "1"})
    r = c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return c
