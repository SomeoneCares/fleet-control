"""Notifications (Settings → Notifications, Slice 4): fleet events delivered to one person, where they asked.

A person chooses how to be reached (a platform an Admin has a direct-message channel for, such as Telegram), gives
their own address on it, and ticks the events they want. Only events their role could act on are offered, and an
event reaches them only when they could see what it is about (a room in a zone they read, a plan they may approve).
The message is the same as a channel's: what happened, a case id and a link; titles only if they ask for them.
Addresses are the person's own: nobody else reads or sets them, and deliveries record the account, not the address.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .auth import PERMISSIONS

# event -> (what the person reads, the permission that makes it theirs, on by default)
PERSONAL_EVENTS: dict[str, tuple[str, Optional[str], bool]] = {
    "room.waiting": ("A Decision Room waits for your decision", "rooms.decide", True),
    "room.second_approval": ("You are named the second approver of a room", "rooms.decide", True),
    "plan.approval": ("A production plan needs your approval", "plans.approve.production", True),
    "ask.answered": ("The fleet answered your question", "ask.use", False),
    "apply.failed": ("An apply failed", "plans.apply.nonprod", False),
    "drift.detected": ("Drift was detected on an instance", "drift.read", False),
    "assurance.no_evidence": ("An assurance check found No evidence", "assurance.read", False),
}
_TELEGRAM = re.compile(r"^-?\d{3,20}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class NotificationError(ValueError):
    pass


def offered(role: str) -> list[str]:
    """The events a role could act on, in a fixed order."""
    return [e for e, (_, perm, _) in PERSONAL_EVENTS.items() if perm is None or role in PERMISSIONS.get(perm, ())]


def defaults(role: str) -> dict[str, Any]:
    return {"via": None, "addresses": {}, "events": {e: PERSONAL_EVENTS[e][2] for e in offered(role)}, "show_titles": False}


def effective(prefs: Optional[dict], role: str) -> dict[str, Any]:
    """A person's preferences over the defaults, trimmed to what their role is offered today (a role change never
    leaves an event they can no longer act on switched on)."""
    base = defaults(role)
    prefs = prefs or {}
    events = {e: bool((prefs.get("events") or {}).get(e, on)) for e, on in base["events"].items()}
    return {"via": prefs.get("via"), "addresses": dict(prefs.get("addresses") or {}), "events": events,
            "show_titles": bool(prefs.get("show_titles", False))}


def check_address(platform: str, address: str) -> str:
    address = (address or "").strip()
    if not address:
        raise NotificationError("give your address on that platform")
    if platform == "telegram" and not _TELEGRAM.match(address):
        raise NotificationError("a Telegram address is your numeric user id (ask @userinfobot), e.g. 123456789")
    if platform == "email" and not _EMAIL.match(address):
        raise NotificationError("that is not an email address")
    if len(address) > 120 or "{" in address or "}" in address:
        raise NotificationError("that address cannot be used")
    return address


def update(current: dict, role: str, *, via: Optional[str] = None, address: Optional[str] = None,
           events: Optional[dict[str, bool]] = None, show_titles: Optional[bool] = None, clear: bool = False) -> dict:
    """Apply a person's change to their preferences. ``via`` names the platform; ``address`` is theirs on it."""
    prefs = effective(current, role)
    if clear:
        return defaults(role)
    if via is not None:
        via = via.strip().lower() or None
        prefs["via"] = via
    if address is not None:
        if not prefs["via"]:
            raise NotificationError("choose how to be reached first")
        prefs["addresses"][prefs["via"]] = check_address(prefs["via"], address)
    if events is not None:
        unknown = sorted(set(events) - set(prefs["events"]))
        if unknown:
            raise NotificationError(f"not offered to your role: {', '.join(unknown)}")
        prefs["events"].update({k: bool(v) for k, v in events.items()})
    if show_titles is not None:
        prefs["show_titles"] = bool(show_titles)
    return prefs


def reach(prefs: dict, event: str) -> Optional[tuple[str, str]]:
    """(platform, address) when this person wants this event and can be reached; None otherwise."""
    if not prefs["events"].get(event):
        return None
    via = prefs.get("via")
    address = (prefs.get("addresses") or {}).get(via) if via else None
    return (via, address) if via and address else None
