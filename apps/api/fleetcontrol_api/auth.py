"""Sign-in, sessions and the five roles (build document §2.2: five roles, not nine).

Local accounts only for now; OIDC comes later and maps onto the same roles. Passwords are hashed with
scrypt from the standard library. Sessions are opaque random tokens in an HttpOnly cookie; the server
keeps only their SHA-256.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ROLES: dict[str, tuple[str, str]] = {
    "admin": ("Admin", "Everything, including instances, people and access, and production approvals."),
    "fleet_architect": ("Fleet Architect", "Design fleets, edit blueprints, create plans, apply to lab and staging."),
    "operator": ("Operator", "Monitor instances and drift, import, resolve drift, approve and apply staging, apply approved production plans."),
    "approver": ("Approver", "Approve production applies; decide in Decision Rooms (Workspace)."),
    "viewer": ("Viewer", "Read-only: blueprints, rooms, outputs and the audit log (Workspace)."),
}

# Admin, architects and operators use the admin portal; approvers and viewers get the Workspace.
ADMIN_PORTAL = frozenset({"admin", "fleet_architect", "operator"})

_ALL = frozenset(ROLES)
PERMISSIONS: dict[str, frozenset[str]] = {
    "instances.read": ADMIN_PORTAL,
    "instances.connect": frozenset({"admin"}),
    "instances.operate": ADMIN_PORTAL,  # import live profiles, drift scans
    "blueprints.read": ADMIN_PORTAL | {"viewer"},
    "blueprints.write": frozenset({"admin", "fleet_architect"}),
    "plans.read": _ALL,  # approvers must be able to read what they approve
    "plans.create": frozenset({"admin", "fleet_architect"}),
    "plans.approve.nonprod": ADMIN_PORTAL,
    "plans.approve.production": frozenset({"admin", "approver"}),
    "plans.apply.nonprod": ADMIN_PORTAL,
    "plans.apply.production": frozenset({"admin", "operator"}),
    "drift.read": ADMIN_PORTAL,
    "drift.resolve": ADMIN_PORTAL,  # accepting into a blueprint also needs blueprints.write
    "audit.read": ADMIN_PORTAL | {"viewer"},
    "users.read": frozenset({"admin"}),
    "users.manage": frozenset({"admin"}),  # also: see and revoke everyone's API tokens
    "rooms.read": _ALL,  # Decision Rooms; the room's content zone still decides what is shown (rooms.py)
    "rooms.open": ADMIN_PORTAL,
    "rooms.decide": frozenset({"admin", "approver"}),  # build document §2.2: Approvers decide in Decision Rooms
    "content.read": _ALL,  # zones gate what each person actually sees (content.py)
    "content.manage": frozenset({"admin", "fleet_architect"}),
    "messaging.read": ADMIN_PORTAL,  # channels, delivery rules and what was sent
    "messaging.manage": frozenset({"admin"}),  # build document §2.2: Admins own messaging
    "ask.use": ADMIN_PORTAL | {"approver"},
    "workflows.run": ADMIN_PORTAL,  # start and stop workflow runs; a run's human gates are decided by the role each names  # Ask the fleet: each question is an agent run, so not Viewers
    "tests.run": ADMIN_PORTAL,  # Test Lab: run tests on lab and staging instances, and stop a run
    "tests.manage": frozenset({"admin"}),  # delete a test run: it erases history, so Admin only
    "assurance.read": ADMIN_PORTAL,
    "settings.read": ADMIN_PORTAL,  # Settings → General and Approvals (everyone manages their own API tokens)
    "settings.manage": frozenset({"admin"}),
}

MIN_PASSWORD_LENGTH = 12
SESSION_SECONDS = 12 * 3600
LOCKOUT_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60

_N, _R, _P, _DKLEN = 2**14, 8, 1, 32


def allowed(role: str, permission: str) -> bool:
    return role in PERMISSIONS.get(permission, frozenset())


def permissions_for(role: str) -> list[str]:
    return sorted(p for p, roles in PERMISSIONS.items() if role in roles)


def role_label(role: str) -> str:
    return ROLES[role][0]


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, dk = stored.split("$")
        if scheme != "scrypt":
            return False
        want = base64.b64decode(dk)
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(want))
        return hmac.compare_digest(got, want)
    except (ValueError, TypeError):
        return False


# Verified against when the email is unknown, so a wrong email costs the same time as a wrong password.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"passwords need at least {MIN_PASSWORD_LENGTH} characters")


def new_password() -> str:
    """A one-time password for a new or reset account (shown once, never stored in clear)."""
    return secrets.token_urlsafe(12)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
