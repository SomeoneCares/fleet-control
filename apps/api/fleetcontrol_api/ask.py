"""Ask the fleet (build document §9, Slice 4): a question answered from content the person may see, sources shown.

The bound is enforced here, not asked of the model. Fleet Control chooses the sources itself — files, fleet outputs
and Decision Rooms — from the zones the person may read AND the orchestrator agent is granted by an applied
blueprint (``content_zones``), numbers them S1, S2, … and sends only those. The orchestrator must answer from them
and cite them; citations are checked against what was sent, and an invented one is dropped and said so.

What the orchestrator cannot be stopped from doing is calling its own tools. The run's transcript says whether it
did: an answer is "Evidence found" only when it cites what was sent and called nothing else; a tool call, or a
missing transcript, makes it "Not verifiable", and an answer that cites nothing is "No evidence".
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Iterable, Optional

from .content import CLASSIFICATIONS

MAX_SOURCES = 8
SOURCE_CHARS = 3_000  # one source's excerpt
BUDGET_CHARS = 24_000  # all sources together
HISTORY_TURNS = 3  # earlier answers sent with a follow-up
QUESTION_MAX = 1_000

_WORD = re.compile(r"[a-z0-9](?:[a-z0-9_.-]*[a-z0-9])?")
_STOP = frozenset("""
a an and are as at be been but by can could did do does for from had has have how i if in into is it its me my no not
of on or our please show so tell than that the their them then there these they this those to us was we were what when
where which who why will with would you your about any all also give list many much some draft write summary summarise
summarize report make paste weekly into could should
""".split())


class AskError(ValueError):
    pass


def terms(text: str) -> list[str]:
    """Search terms: lowercase words of 3+ characters, common words dropped. A joined word is kept whole and in its
    parts, so case ids like aml-2026-0412 match exactly and "Cyprus-registered" still matches "Cyprus"."""
    out = []
    for w in _WORD.findall((text or "").lower()):
        parts = [p for p in re.split(r"[_.-]", w) if p] if re.search(r"[_.-]", w) else []
        for t in [w] + parts:
            if len(t) >= 3 and t not in _STOP:
                out.append(t)
    return out


def _excerpt(text: str, wanted: set[str], size: int = SOURCE_CHARS) -> str:
    """The part of a text that matches best: paragraphs scored by hits, the best one grown with its neighbours."""
    text = (text or "").strip()
    if len(text) <= size:
        return text
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()] or [text]
    scores = [sum(1 for w in terms(p) if w in wanted) for p in paras]
    best = max(range(len(paras)), key=lambda i: (scores[i], -i))
    lo = hi = best
    out = paras[best][:size]
    while True:
        grown = False
        for j in (hi + 1, lo - 1):
            if 0 <= j < len(paras) and len(out) + len(paras[j]) + 2 <= size:
                out = out + "\n\n" + paras[j] if j > hi else paras[j] + "\n\n" + out
                lo, hi, grown = min(lo, j), max(hi, j), True
        if not grown:
            break
    return out


def select_sources(question: str, candidates: list[dict], *, carry: Iterable[str] = (), max_sources: int = MAX_SOURCES,
                   budget: int = BUDGET_CHARS) -> list[dict]:
    """The candidates that match the question best, as numbered sources with excerpts.

    A candidate is {key, kind, ref, label, zone, classification, text}. ``carry`` are keys cited earlier in the
    conversation: they come first, so a follow-up ("draft a summary of that") keeps what it refers to."""
    wanted = set(terms(question))
    docs = [(c, terms(c["label"]), terms(c.get("text") or "")) for c in candidates]
    n = len(docs) or 1
    df = {w: sum(1 for _, lab, body in docs if w in lab or w in body) for w in wanted}
    idf = {w: math.log(1 + n / (1 + df[w])) + 0.1 for w in wanted}

    def score(lab: list[str], body: list[str]) -> float:
        s = 0.0
        for w in wanted:
            tf = body.count(w)
            s += (1 + math.log(tf)) * idf[w] if tf else 0.0
            s += 3 * idf[w] if w in lab else 0.0
        return s

    carried = [c for c in candidates if c["key"] in set(carry)]
    ranked = sorted(((score(lab, body), c) for c, lab, body in docs if c["key"] not in set(carry)),
                    key=lambda x: -x[0])
    chosen = carried + [c for s, c in ranked if s > 0]
    out, used = [], 0
    for c in chosen:
        if len(out) >= max_sources:
            break
        excerpt = _excerpt(c.get("text") or "", wanted)
        if used + len(excerpt) > budget:
            excerpt = excerpt[: max(0, budget - used)]
        if not excerpt:
            break
        used += len(excerpt)
        out.append({"id": f"S{len(out) + 1}", "key": c["key"], "kind": c["kind"], "ref": c["ref"], "label": c["label"],
                    "zone": c["zone"], "classification": c.get("classification"), "excerpt": excerpt,
                    "truncated": len(excerpt) < len((c.get("text") or "").strip())})
    return out


def instructions(*, asker: str, role: str, sources: list[dict]) -> str:
    """The run's instructions: who is asking, the only sources there are, and the answer contract."""
    blocks = "\n\n".join(
        f"[{s['id']}] {s['label']} ({s['kind']}, zone {s['zone']}"
        + (f", {s['classification']}" if s.get("classification") else "") + ")\n" + s["excerpt"]
        for s in sources) or "(no sources: nothing this person may see matches the question)"
    return f"""You answer questions for {asker} ({role}) on behalf of Fleet Control, the control plane of this agent fleet.

Answer ONLY from the numbered sources below. They are everything this person is allowed to see that matches the
question. Do not use tools, memory, or anything else you know. If the sources do not answer the question, say so
plainly and say what is missing; never guess. Cite every statement with the source ids it rests on, like [S1].

Reply with one JSON object and nothing else:
{{"answer": "<your answer, in plain prose, with [S1]-style citations>", "sources": ["S1", "S2"]}}

SOURCES
{blocks}
"""


def request_text(question: str, history: list[dict]) -> str:
    """The run's input: the new question, after the last few questions and answers of the conversation."""
    earlier = [t for t in history if t.get("status") == "answered"][-HISTORY_TURNS:]
    lines = []
    for t in earlier:
        lines += [f"Earlier question: {t['question']}", f"Your earlier answer: {(t.get('answer') or '')[:2000]}", ""]
    return "\n".join(lines + [f"Question: {question}"])


def _json_object(text: str) -> Optional[dict]:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    for candidate in ([fence.group(1)] if fence else []) + [text, text[text.find("{"): text.rfind("}") + 1]]:
        try:
            value = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def parse_answer(output: str, source_ids: Iterable[str]) -> dict:
    """{answer, cited, dropped, format}: the answer text, the source ids it cites that were really sent, and the ones it
    made up. A reply that is not the JSON asked for is still used as text, and says so (``format: "text"``)."""
    known = set(source_ids)
    obj = _json_object(output)
    if obj is not None and isinstance(obj.get("answer"), str) and obj["answer"].strip():
        answer, listed, fmt = obj["answer"].strip(), obj.get("sources") or [], "json"
    elif (output or "").strip():
        answer, listed, fmt = output.strip(), [], "text"
    else:
        raise AskError("the orchestrator returned an empty answer")
    named = [str(x).strip() for x in listed if isinstance(x, (str, int))] + re.findall(r"\[(S\d+)\]", answer)
    cited, dropped = [], []
    for sid in named:
        target = cited if sid in known else dropped
        if sid not in target:
            target.append(sid)
    return {"answer": answer, "cited": sorted(cited, key=lambda s: int(s[1:])), "dropped": dropped, "format": fmt}


def grounding(cited: list[str], tool_calls: Optional[list[dict]], *, evidence_error: Optional[str] = None) -> dict:
    """One of the four verdicts for the answer as a whole, with the reason."""
    if not cited:
        return {"verdict": "No evidence", "detail": "the answer cites none of the sources it was given"}
    if tool_calls is None:
        why = f" ({evidence_error})" if evidence_error else ""
        return {"verdict": "Not verifiable",
                "detail": f"it cites {', '.join(cited)}, but there is no transcript to show it used nothing else{why}"}
    if tool_calls:
        names = sorted({c.get("name") or "?" for c in tool_calls})
        return {"verdict": "Not verifiable",
                "detail": f"it also called {', '.join(names)}: what those returned was not bounded by your access"}
    return {"verdict": "Evidence found", "detail": f"it rests on {', '.join(cited)} and called no tools"}


def classification_of(classifications: Iterable[Optional[str]]) -> str:
    """The strictest classification among the sources (what is produced from content carries its classification)."""
    rank = {c: i for i, c in enumerate(CLASSIFICATIONS)}
    found = [c for c in classifications if c in rank]
    return max(found, key=rank.__getitem__) if found else CLASSIFICATIONS[0]


def _readers(zone: dict) -> set[str]:
    return set(zone.get("read_roles") or []) | {"admin"}


def save_targets(zones: list[dict], *, mine: Iterable[str], cited_zones: Iterable[str]) -> list[str]:
    """Zones an answer may be saved into: ones the person may read whose readers could already read every zone the
    answer cites, so saving never shows anyone content they could not see before."""
    by_id = {z["id"]: z for z in zones}
    sources = [by_id[z] for z in set(cited_zones) if z in by_id]
    out = []
    for zid in mine:
        z = by_id.get(zid)
        if z and all(_readers(z) <= _readers(s) for s in sources):
            out.append(zid)
    return sorted(out)


def granted_zones(profile: str, blueprints: list[dict]) -> list[str]:
    """Zones an agent running as ``profile`` is granted by the given (applied) blueprints."""
    out: set[str] = set()
    for parsed in blueprints:
        for agent in parsed.get("agents") or []:
            if (agent.get("hermes_profile") or agent.get("id")) == profile:
                out |= set(agent.get("content_zones") or [])
    return sorted(out)


def room_text(room: dict) -> str:
    """What a Decision Room says, as text a source excerpt can come from."""
    parts = [f"Question: {room['question']}"]
    if room.get("case"):
        parts.append(f"Case: {room['case']}")
    parts.append(f"Status: {room['status']}; options: " + "; ".join(o["label"] for o in room.get("options") or []))
    parts += [f"Evidence ({e.get('basis') or e['kind']}): {e['label']}" for e in room.get("evidence") or []]
    parts += [f"Finding by {f['agent']} ({f.get('basis') or 'interpretation'}): {f['text']}" for f in room.get("findings") or []]
    labels = {o["id"]: o["label"] for o in room.get("options") or []}
    parts += [f"Decision by {d['by']}: {labels.get(d['option'], d['option'])} — {d['rationale']}" for d in room.get("decisions") or []]
    return "\n\n".join(parts)


def check_question(text: str) -> str:
    text = (text or "").strip()
    if not (3 <= len(text) <= QUESTION_MAX):
        raise AskError(f"a question is between 3 and {QUESTION_MAX} characters")
    return text


def thread_row(t: dict) -> dict[str, Any]:
    last = t["turns"][-1] if t["turns"] else None
    return {"id": t["id"], "title": t["turns"][0]["question"][:120] if t["turns"] else "", "created_at": t["created_at"],
            "updated_at": t["updated_at"], "turns": len(t["turns"]), "status": last["status"] if last else None}


ORCHESTRATOR_BLUEPRINT = "fleet-control-orchestrator"
ORCHESTRATOR_PROFILE = "fc-orchestrator"


def orchestrator_blueprint(model: dict, *, zones: list[str], instance_id: str, environment: str, owner: str, version: int):
    """Ask the fleet's orchestrator as a blueprint: one profile with no skills and no tools, granted ``zones``."""
    from fleetcontrol_blueprint import Blueprint

    return Blueprint.model_validate({
        "metadata": {"name": ORCHESTRATOR_BLUEPRINT, "version": version, "owner": owner,
                     "description": "Ask the fleet: one profile that answers questions from the sources Fleet Control sends it. It has no tools."},
        "requires": {"hermes": ">=0.21", "capabilities": ["runs", "profiles.write"], "agent": "required"},
        "mission": "Answer people's questions from the content they may see, citing every source.",
        "agents": [{
            "id": ORCHESTRATOR_PROFILE,
            "role": "Fleet Control orchestrator: answers questions from the sources it is given; never acts.",
            "model": {"provider": model["provider"], "name": model["name"]},
            "soul": {
                "objective": "Answer the question from the numbered sources in the instructions, citing each one used.",
                "principles": [
                    "Answer with one JSON object that follows the contract in the instructions, and nothing else.",
                    "Say plainly when the sources do not answer the question, and what is missing.",
                ],
                "boundaries": ["Never call tools or use anything but the sources you are given.",
                               "Never guess, and never cite a source you were not given."],
            },
            "skills": [],
            "toolsets": [],
            "content_zones": sorted(set(zones)),
        }],
        "targets": [{"instance": instance_id, "environment": environment}],
    })
