"""Content zones (build document §9, Slice 4): the files agents may read, and who may read them.

A zone is the unit of access. People reach a zone through their role; agents reach it because the blueprint
gives them that zone (``content_zones``), and an agent on a redacted-only model sees redacted summaries only —
the instance redacts before the agent sees anything, so Fleet Control only records the intent. Classification
(internal, confidential, restricted) travels with each file and with everything produced from it; it is shown
and recorded, while the zone is what grants or denies the read.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

CLASSIFICATIONS = ("internal", "confidential", "restricted")
_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
MAX_TEXT = 200_000  # a file's text is kept for Ask the fleet and previews; bigger files are metadata only


class ContentError(ValueError):
    pass


def zone_id(value: str) -> str:
    """"Case files" -> "case-files"; ids are what blueprints reference in ``content_zones``."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    if not _ID.match(slug):
        raise ContentError("a zone id is lowercase words joined by hyphens, 2 to 63 characters, starting with a letter")
    return slug


def check_classification(value: str) -> str:
    if value not in CLASSIFICATIONS:
        raise ContentError(f"classification must be one of: {', '.join(CLASSIFICATIONS)}")
    return value


def may_read(zone: dict, role: str) -> bool:
    """An Admin always may; everyone else needs their role on the zone."""
    return role == "admin" or role in (zone.get("read_roles") or [])


def readable_zones(zones: Iterable[dict], role: str) -> list[str]:
    return [z["id"] for z in zones if may_read(z, role)]


def agent_access(zone_id_: str, blueprints: Iterable[dict]) -> list[dict]:
    """Which blueprint agents read this zone, and whether their model may only see redacted input."""
    out: list[dict] = []
    for parsed in blueprints:
        name = parsed["metadata"]["name"]
        redacting = any(p.get("kind") == "data-residency" and p.get("enforcement") == "block" for p in parsed.get("policies") or [])
        for agent in parsed.get("agents") or []:
            if zone_id_ not in (agent.get("content_zones") or []):
                continue
            redacted = ((agent.get("model") or {}).get("data_class") == "redacted-only") and redacting
            row = {"agent": agent["id"], "blueprint": name, "redacted_only": redacted}
            if row not in out:
                out.append(row)
    return out


def new_zone(*, id: str, name: str, description: str = "", read_roles: Optional[list[str]] = None, managed: bool = False,
             source: Optional[str] = None, by: str, at: float) -> dict:
    zone = {
        "id": zone_id(id),
        "name": (name or "").strip() or zone_id(id),
        "description": (description or "").strip(),
        "read_roles": sorted(set(read_roles or [])),
        "managed": bool(managed),  # synced from an MCP source: files and access are not edited here
        "source": source,
        "created_by": by,
        "created_at": at,
    }
    if len(zone["name"]) > 80:
        raise ContentError("a zone name is at most 80 characters")
    return zone


def new_file(*, zone: str, name: str, classification: str, text: Optional[str], by: str, at: float, file_id: str) -> dict:
    name = (name or "").strip()
    if not name or len(name) > 200:
        raise ContentError("a file needs a name of at most 200 characters")
    if text is not None and len(text) > MAX_TEXT:
        raise ContentError(f"the text is longer than {MAX_TEXT:,} characters; store it on the instance and reference it instead")
    return {
        "id": file_id,
        "zone": zone,
        "name": name,
        "classification": check_classification(classification),
        "text": text,
        "size": len(text) if text is not None else None,
        "uploaded_by": by,
        "at": at,
    }


def file_row(f: dict, *, with_text: bool = False) -> dict[str, Any]:
    """A file as the screens show it; the text only where it was asked for."""
    row = {k: f[k] for k in ("id", "zone", "name", "classification", "size", "uploaded_by", "at")}
    if with_text:
        row["text"] = f.get("text")
    return row
