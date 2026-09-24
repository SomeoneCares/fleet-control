"""Messaging (build document §8, Slice 4): fleet events delivered to people where they already are.

A channel is a messaging platform a Hermes gateway on an instance has connected (Telegram, Slack, email, …),
optionally a particular chat on it. Fleet Control never talks to the platform: the instance's Fleet Control Agent
keeps one ``deliver_only`` webhook route per channel (``fc-<channel>``), whose secret never leaves the host, and posts
each message to it on its own loopback; Hermes delivers the text as-is, with no agent run.

Which events go where is part of the blueprint (``delivery: [{when, to: "<platform>:<channel>", template}]``), so it
promotes with the blueprint: the newest applied version of each blueprint is what delivers.

Messaging delivers; it never decides. A message carries what happened and a link back into the portal. By default it
names no case content: a room's question or an output's name travel only on channels marked to show titles. Evidence,
findings and decisions never leave the portal, and a reply in chat is not a decision.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

EVENTS: dict[str, str] = {  # blueprint DeliveryRule.when -> what a person reads
    "decision_room.opened": "Decision Room opened",
    "approval.second_needed": "Second approval needed",
    "output.shared": "Fleet output shared",
    "assurance.no_evidence": "Assurance: No evidence",
    "assurance.policy_blocked": "Assurance: Policy blocked",
    "drift.detected": "Drift detected",
    "apply.completed": "Apply completed",
    "apply.failed": "Apply failed",
}
TEMPLATES = ("decision-request", "approval-request", "output-summary", "alert")
MAX_TEXT = 1_000
_ID = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


class MessagingError(ValueError):
    pass


def channel_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")[:41].rstrip("-")
    if not _ID.match(slug):
        raise MessagingError("a channel name needs a few letters (it becomes the id blueprints refer to)")
    return slug


def new_channel(*, name: str, platform: str, instance_id: str, by: str, at: float, chat_id: Optional[str] = None,
                audience: str = "", show_titles: bool = False) -> dict:
    cid = channel_id(name)
    platform = (platform or "").strip().lower()
    if not re.match(r"^[a-z][a-z0-9_]{1,30}$", platform):
        raise MessagingError("choose the messaging platform the channel delivers through")
    return {"id": cid, "name": name.strip(), "platform": platform, "ref": f"{platform}:{cid}", "route": f"fc-{cid}",
            "instance_id": instance_id, "chat_id": (chat_id or "").strip() or None, "audience": (audience or "").strip()[:120],
            "show_titles": bool(show_titles), "enabled": True, "created_by": by, "created_at": at, "route_job": None}


def merge_gateway(platforms: list[dict], gateway: Optional[dict]) -> list[dict]:
    """Each platform's state as the gateway itself records it, when its record is alive and fresh. The dashboard's
    view is kept as ``dashboard_state``: a dashboard that outlived a Hermes update calls a live gateway stopped."""
    if not gateway or not gateway.get("alive"):
        return platforms
    own = gateway.get("platforms") or {}
    out = []
    for p in platforms:
        g = own.get(p.get("id")) or {}
        if g.get("state"):
            p = {**p, "dashboard_state": p.get("state"), "state": g["state"], "gateway_running": True,
                 "error_message": g.get("error_message") or (None if g["state"] == "connected" else p.get("error_message"))}
        out.append(p)
    return out


def channel_health(channel: dict, state: Optional[dict]) -> dict:
    """Whether a channel can deliver, from the last messaging discovery of its instance, and what to do if not."""
    if not channel.get("enabled", True):
        return {"status": "disabled", "detail": "switched off in Fleet Control"}
    if not state:
        return {"status": "unknown", "detail": f"not discovered yet: Discover on {channel['instance_id']}"}
    hooks = state.get("webhooks") or {}
    if not hooks.get("enabled"):
        return {"status": "webhooks_off", "detail": f"the webhook platform is off on {channel['instance_id']}: Enable webhooks"}
    platform = next((p for p in state.get("platforms") or [] if p.get("id") == channel["platform"]), None)
    if not platform or not platform.get("configured"):
        return {"status": "platform_missing", "detail": f"{channel['platform']} is not configured on {channel['instance_id']}"}
    if platform.get("state") not in ("connected", "running", None):  # e.g. fatal, disconnected, gateway_stopped
        return {"status": "platform_down", "detail": f"{channel['platform']} is {platform.get('state') or 'not running'}"
                                                     + (f": {platform['error_message']}" if platform.get("error_message") else "")}
    route = next((r for r in hooks.get("routes") or [] if r.get("name") == channel["route"]), None)
    if not route or not route.get("signable"):
        return {"status": "route_missing", "detail": f"the delivery route {channel['route']} is missing on the instance: Recreate route"}
    if route.get("enabled") is False:
        return {"status": "route_off", "detail": f"{channel['route']} is switched off on the instance"}
    return {"status": "ready", "detail": None}


def rules_from(blueprints: Iterable[dict], channels: Iterable[dict]) -> list[dict]:
    """Every delivery rule of the given (applied) blueprints, with the channel its ``to`` names, if there is one."""
    by_ref = {c["ref"]: c for c in channels}
    out = []
    for parsed in blueprints:
        meta = parsed["metadata"]
        for n, r in enumerate(parsed.get("delivery") or []):
            channel = by_ref.get(r["to"])
            out.append({"blueprint": meta["name"], "version": meta.get("version"), "n": n, "when": r["when"],
                        "event": EVENTS.get(r["when"], r["when"]), "to": r["to"], "template": r["template"],
                        "enabled": r.get("enabled", True), "channel": channel["id"] if channel else None})
    return out


def render(event: str, template: str, ctx: dict, *, show_titles: bool, portal_url: str) -> str:
    """The message: what happened, where, and a link back. ``ctx`` carries ``title`` (case content: shown only on a
    channel that shows titles), ``case``, ``instance``, ``detail`` (Fleet Control's own words) and ``link`` (a path)."""
    head = {"decision-request": "Decision needed", "approval-request": "Second approval needed",
            "output-summary": "New fleet output"}.get(template) or EVENTS.get(event, event)
    lines = [f"Fleet Control · {head}"]
    if ctx.get("title") and show_titles:
        lines.append(str(ctx["title"])[:300])
    facts = [f"case {ctx['case']}" if ctx.get("case") else None, f"on {ctx['instance']}" if ctx.get("instance") else None]
    if any(facts):
        lines.append(", ".join(f for f in facts if f))
    if ctx.get("detail"):
        lines.append(str(ctx["detail"])[:300])
    if ctx.get("link"):
        lines.append(f"Open: {portal_url.rstrip('/')}{ctx['link']}")
    if template in ("decision-request", "approval-request"):
        lines.append("Decide in the workspace; a reply here is not a decision.")
    return "\n".join(lines)[:MAX_TEXT]


def test_text(channel: dict, by: str, portal_url: str) -> str:
    return (f"Fleet Control · Test message\nChannel {channel['name']} ({channel['ref']}) on {channel['instance_id']}, "
            f"sent by {by}.\nOpen: {portal_url.rstrip('/')}/messaging")


def new_delivery(*, delivery_id: str, channel: dict, event: str, key: str, text: str, at: float, rule: Optional[dict] = None,
                 by: Optional[str] = None) -> dict[str, Any]:
    return {"id": delivery_id, "channel": channel["id"], "instance_id": channel["instance_id"], "event": event, "key": key,
            "text": text, "status": "queued", "job_id": None, "error": None, "at": at, "finished_at": None,
            "rule": {k: rule[k] for k in ("blueprint", "version", "n")} if rule else None, "by": by}
