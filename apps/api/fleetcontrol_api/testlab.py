"""Test Lab and Assurance (build document §6 and §9, Slice 3).

A test runs its scenario on the target agent's profile on a lab or staging instance. The Fleet Control Agent
returns the run's output, usage and duration, and every tool call from the Hermes session transcript. Each
check is judged against that evidence: pass, fail, or not verifiable when the evidence is missing. The
checks also become claims with one of the four assurance verdicts: Evidence found, No evidence, Not
verifiable, Policy blocked. None of this judges whether the content is right, only whether the run did what
is claimed; the copy must never suggest more.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

VERDICTS = ("Evidence found", "No evidence", "Not verifiable", "Policy blocked")
_OUTCOME_VERDICT = {"pass": "Evidence found", "fail": "No evidence", "not_verifiable": "Not verifiable"}
_NOT_TOOLS = {"e.g", "i.e", "etc"}
_FILE_EXT = {"json", "md", "docx", "doc", "pdf", "csv", "txt", "yaml", "yml", "xlsx", "xls", "png", "jpg", "html", "py", "log"}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def tool_matches(expected: str, called: str) -> bool:
    """Does a tool the run called match a tool a test names? Hermes calls MCP tools ``mcp_<server>__<tool>``,
    so "opensanctions.search" matches "mcp_opensanctions__search"; "web.fetch" matches "web_fetch"."""
    names = {called}
    low = str(called).lower()
    if low.startswith("mcp_") and "__" in low[4:]:
        server, tool = low[4:].split("__", 1)
        names |= {f"{server}.{tool}", f"{server}:{tool}"}
    want = _norm(expected)
    return bool(want) and any(_norm(n) == want for n in names)


def output_json(text: str) -> Optional[Any]:
    """The JSON object in an output (bare, fenced or inside prose), or None."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(candidate) if candidate else None
    except json.JSONDecodeError:
        return None


def mentioned_tools(output: str, limit: int = 5) -> list[str]:
    """Rule-based claim extraction (build document §6, step 2): tool-like names an output mentions, such as
    ``opensanctions.search``. Abbreviations and file names are left out."""
    found: list[str] = []
    for m in re.finditer(r"`?\b([a-z][a-z0-9_]{1,40}(?:[.:][a-z0-9_]{2,40})+)\b`?", output or ""):
        name = m.group(1)
        if name in _NOT_TOOLS or name.rsplit(".", 1)[-1] in _FILE_EXT or name in found:
            continue
        found.append(name)
        if len(found) >= limit:
            break
    return found


def _check(checks: list, claims: list, *, cid: str, kind: str, subject: str, outcome: str, detail: str,
           claim: Optional[str], verdict: Optional[str] = None) -> None:
    checks.append({"id": cid, "kind": kind, "subject": subject, "outcome": outcome, "detail": detail})
    if claim:
        claims.append({"claim": claim, "verdict": verdict or _OUTCOME_VERDICT[outcome], "check": cid, "detail": detail})


def evaluate(test: dict, agent: Optional[dict], result: dict) -> dict:
    """Judge one test run. ``test`` is the blueprint test, ``agent`` its target agent (for the output contract),
    ``result`` what the run_test job returned. Returns {status, checks, claims}; status is passed, failed,
    not_verifiable (nothing failed but some evidence is missing) or error (the run itself did not complete)."""
    checks: list[dict] = []
    claims: list[dict] = []
    if result.get("status") != "completed":
        detail = result.get("error") or f"the run ended {result.get('status') or 'without a result'}"
        checks.append({"id": "run", "kind": "run", "subject": "the run", "outcome": "fail", "detail": detail})
        return {"status": "error", "checks": checks, "claims": claims}

    calls = result.get("tool_calls")
    evidence = calls is not None
    names = [c.get("name", "") for c in calls or []]
    blocked = list(result.get("blocked_tools") or [])  # tool calls the Fleet Control plugin blocked (policy)
    missing = "no session transcript for this run" + (f" ({result['evidence_error']})" if result.get("evidence_error") else "")
    output = result.get("output") or ""

    for tool in test.get("required_tools") or []:
        hits = [n for n in names if tool_matches(tool, n)]
        was_blocked = any(tool_matches(tool, b) for b in blocked)
        if not evidence:
            outcome, detail = "not_verifiable", missing
        elif hits:
            outcome, detail = "pass", f"called {len(hits)} time(s) as {hits[0]}"
        else:
            outcome, detail = "fail", f"not among the {len(names)} tool call(s) of the run"
        if was_blocked:
            outcome, detail = "fail", "the call was blocked by a Fleet Control policy"
        _check(checks, claims, cid=f"required:{tool}", kind="required_tool", subject=tool, outcome=outcome, detail=detail,
               claim=f"Called {tool}", verdict="Policy blocked" if was_blocked else None)

    for tool in test.get("forbidden_tools") or []:
        hits = [n for n in names if tool_matches(tool, n)]
        was_blocked = any(tool_matches(tool, b) for b in blocked)
        if was_blocked:
            outcome, detail = "pass", "attempted, and blocked by a Fleet Control policy"
        elif not evidence:
            outcome, detail = "not_verifiable", missing
        elif hits:
            outcome, detail = "fail", f"called {len(hits)} time(s) as {hits[0]}"
        else:
            outcome, detail = "pass", f"not among the {len(names)} tool call(s) of the run"
        _check(checks, claims, cid=f"forbidden:{tool}", kind="forbidden_tool", subject=tool, outcome=outcome, detail=detail,
               claim=f"Did not call {tool}", verdict="Policy blocked" if was_blocked else None)

    artifact = test.get("expected_artifact")
    if artifact:
        where = next((c.get("name") for c in calls or [] if artifact in (c.get("arguments") or "") or artifact in (c.get("result") or "")), None)
        if not evidence:
            outcome, detail = "not_verifiable", missing
        elif where:
            outcome, detail = "pass", f"named in a {where} call"
        else:
            outcome, detail = "fail", "no tool call wrote or named it"
        _check(checks, claims, cid="artifact", kind="artifact", subject=artifact, outcome=outcome, detail=detail, claim=f"Produced {artifact}")

    evaluator = test.get("evaluator") or "schema"
    if evaluator == "schema":
        required = (((agent or {}).get("soul") or {}).get("output_contract") or {}).get("required") or []
        if not required:
            _check(checks, claims, cid="output", kind="output", subject="output contract", outcome="not_verifiable",
                   detail="the agent has no output contract with required fields", claim=None)
        else:
            data = output_json(output)
            gaps = [k for k in required if not isinstance(data, dict) or k not in data]
            outcome = "fail" if gaps else "pass"
            detail = (f"missing {', '.join(gaps)}" if isinstance(data, dict) else "the output is not a JSON object") if gaps else \
                f"has {', '.join(required)}"
            _check(checks, claims, cid="output", kind="output", subject="output contract", outcome=outcome, detail=detail,
                   claim="The output follows the agent's contract")
    elif evaluator in ("contains", "exact"):
        expected = test.get("expected") or ""
        ok = (expected in output) if evaluator == "contains" else (output.strip() == expected.strip())
        what = f"contains “{expected}”" if evaluator == "contains" else "is exactly the expected text"
        _check(checks, claims, cid="output", kind="output", subject=f"output {evaluator}", outcome="pass" if ok else "fail",
               detail=("it does" if ok else "it does not"), claim=f"The output {what}")
    elif evaluator == "artifact-exists" and not artifact:
        _check(checks, claims, cid="output", kind="output", subject="artifact", outcome="not_verifiable",
               detail="the test names no expected artifact", claim=None)

    limits = test.get("limits") or {}
    if limits.get("max_seconds") is not None and result.get("duration_s") is not None:
        ok = result["duration_s"] <= limits["max_seconds"]
        _check(checks, claims, cid="limit:time", kind="limit", subject=f"≤ {limits['max_seconds']} s", outcome="pass" if ok else "fail",
               detail=f"took {result['duration_s']} s (polled every 2 s)", claim=None)
    total = (result.get("usage") or {}).get("total_tokens")
    if limits.get("max_tokens") is not None:
        if total is None:
            _check(checks, claims, cid="limit:tokens", kind="limit", subject=f"≤ {limits['max_tokens']:,} tokens",
                   outcome="not_verifiable", detail="the run reported no token usage", claim=None)
        else:
            ok = total <= limits["max_tokens"]
            _check(checks, claims, cid="limit:tokens", kind="limit", subject=f"≤ {limits['max_tokens']:,} tokens",
                   outcome="pass" if ok else "fail", detail=f"used {total:,} tokens", claim=None)

    covered = {c["subject"] for c in checks}
    for name in mentioned_tools(output):  # what the agent says it did, checked against what it did
        if any(tool_matches(name, s) or tool_matches(s, name) for s in covered):
            continue
        hits = [n for n in names if tool_matches(name, n)]
        verdict = "Not verifiable" if not evidence else ("Evidence found" if hits else "No evidence")
        claims.append({"claim": f"Used {name} (the output says so)", "verdict": verdict, "check": None,
                       "detail": missing if not evidence else (f"called as {hits[0]}" if hits else "no such tool call in the run")})

    tool_checks = [c for c in checks if c["kind"] != "limit"]
    if any(c["outcome"] == "fail" for c in checks):
        status = "failed"
    elif any(c["outcome"] == "not_verifiable" for c in tool_checks if c["kind"] != "output" or c["detail"] != "the agent has no output contract with required fields"):
        status = "not_verifiable"
    else:
        status = "passed"
    return {"status": status, "checks": checks, "claims": claims}
