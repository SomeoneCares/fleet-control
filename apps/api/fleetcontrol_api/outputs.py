"""Fleet outputs (build document §4.1, §9 Slice 4): what the fleet produced, and where it came from.

An output lives in a content zone, so the same access decides who sees it: a person reads it when their role
reaches the zone. Each output carries its classification and its provenance — which agent or person produced
it, on which instance, and from which run, decision or question — so nothing in the Workspace is unattributed.
"""

from __future__ import annotations

from typing import Any, Optional

from .content import check_classification

KINDS = ("document", "structured", "graph", "markdown", "summary")
SOURCE_KINDS = ("agent", "ask", "decision", "test", "upload")
MAX_TEXT = 200_000


class OutputError(ValueError):
    pass


def new_output(*, output_id: str, zone: str, name: str, kind: str, classification: str, produced_by: str, at: float,
               text: Optional[str] = None, case: Optional[str] = None, source: Optional[dict] = None,
               instance_id: Optional[str] = None, blueprint: Optional[str] = None) -> dict:
    name = (name or "").strip()
    if not name or len(name) > 200:
        raise OutputError("an output needs a name of at most 200 characters")
    if kind not in KINDS:
        raise OutputError(f"kind must be one of: {', '.join(KINDS)}")
    if not (produced_by or "").strip():
        raise OutputError("an output records who produced it")
    if text is not None and len(text) > MAX_TEXT:
        raise OutputError(f"the text is longer than {MAX_TEXT:,} characters")
    source = dict(source or {"kind": "upload"})
    if source.get("kind") not in SOURCE_KINDS:
        raise OutputError(f"source.kind must be one of: {', '.join(SOURCE_KINDS)}")
    return {
        "id": output_id,
        "zone": zone,
        "name": name,
        "kind": kind,
        "classification": check_classification(classification),
        "produced_by": produced_by.strip(),
        "case": (case or "").strip() or None,
        "blueprint": blueprint,
        "instance_id": instance_id,
        "source": source,
        "text": text,
        "size": len(text) if text is not None else None,
        "at": at,
    }


def output_row(o: dict, *, with_text: bool = False) -> dict[str, Any]:
    row = {k: o.get(k) for k in ("id", "zone", "name", "kind", "classification", "produced_by", "case", "blueprint",
                                 "instance_id", "source", "size", "at")}
    if with_text:
        row["text"] = o.get("text")
    return row


def provenance(o: dict) -> str:
    """One line a person can read: who made it and what it came from."""
    source = o.get("source") or {}
    where = {"agent": "an agent run", "ask": "a question to the fleet", "decision": "a decision record",
             "test": "a test run", "upload": "an upload"}.get(source.get("kind"), "an unknown source")
    ref = f" ({source['ref']})" if source.get("ref") else ""
    on = f" on {o['instance_id']}" if o.get("instance_id") else ""
    return f"{o.get('produced_by', 'someone')} produced it from {where}{ref}{on}"
