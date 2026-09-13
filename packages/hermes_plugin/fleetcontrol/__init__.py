"""Fleet Control plugin for Hermes Agent.

Runs inside the Hermes process. Two jobs:

1. **Evidence**: forward tool-call and subagent lifecycle events (with arguments and
   results, redacted per capture mode) to the local ``fleetctl-agent`` daemon over a
   Unix socket (or a loopback TCP port on Windows). The daemon batches and relays them
   to Fleet Control. This is the only real-time source of child-agent tool evidence:
   the ``/v1/runs`` SSE stream drops it by design (see spike addendum, S4).

2. **Policy**: block or escalate tool calls that violate the blueprint's policies for
   this profile, using ``pre_tool_call``'s documented ``{"action": "block"|"approve"}``
   return contract. Policies are pushed to the plugin by the daemon as a JSON file
   (``$HERMES_HOME/fleetcontrol/policy.json``) so enforcement keeps working if the
   daemon is down (fail-closed on *reading* the policy file is deliberate: a missing
   file means "no policies", an unreadable one means "block nothing, flag loudly").

Hook signatures follow ``website/docs/user-guide/features/hooks.md`` in
hermes-agent 0.21.2. Every callback accepts ``**kwargs`` so new upstream fields are
ignored rather than fatal. The plugin never raises into Hermes: all failures are
logged and swallowed (evidence is best-effort; policy falls back to the last good file).
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import threading
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger("fleetcontrol.plugin")

PLUGIN_VERSION = "0.1.0"
PROTOCOL_VERSION = 1

_MAX_CHARS = int(os.environ.get("FLEETCONTROL_CAPTURE_MAX_CHARS", "12000"))
_CAPTURE = os.environ.get("FLEETCONTROL_CAPTURE", "sanitized")  # metadata | sanitized | full
_SOCKET = os.environ.get("FLEETCONTROL_AGENT_SOCKET", "")  # empty -> default per platform
_PROFILE = os.environ.get("HERMES_PROFILE", "") or os.path.basename(os.environ.get("HERMES_HOME", "")) or "default"

_SECRET_PATTERNS = [
    re.compile(r"(sk|pk|xoxb|xapp|ghp|github_pat)[-_][A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s,}]{6,}"),
]


# ----------------------------------------------------------------------------- transport


def _default_socket_path() -> str:
    if os.name == "nt":
        return "tcp://127.0.0.1:47831"
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    return os.path.join(home, "fleetcontrol", "agent.sock")


class _Emitter:
    """Fire-and-forget event sender. A bounded queue drained by one background thread,
    so a dead daemon can never stall a tool call (same discipline as Hermes's outbound webhooks)."""

    def __init__(self, target: str, maxlen: int = 2000):
        self.target = target
        self._q: list[dict] = []
        self._lock = threading.Lock()
        self._maxlen = maxlen
        self._dropped = 0
        self._thread = threading.Thread(target=self._run, name="fleetcontrol-emit", daemon=True)
        self._thread.start()

    def emit(self, event: dict) -> None:
        with self._lock:
            if len(self._q) >= self._maxlen:
                self._dropped += 1
                self._q.pop(0)
            self._q.append(event)

    def _connect(self) -> Optional[socket.socket]:
        try:
            if self.target.startswith("tcp://"):
                host, port = self.target[6:].rsplit(":", 1)
                s = socket.create_connection((host, int(port)), timeout=2)
            else:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(2)
                s.connect(self.target)
            return s
        except OSError:
            return None

    def _run(self) -> None:
        sock: Optional[socket.socket] = None
        backoff = 1.0
        while True:
            with self._lock:
                batch, self._q = self._q[:200], self._q[200:]
            if not batch:
                time.sleep(0.2)
                continue
            if sock is None:
                sock = self._connect()
                if sock is None:
                    # keep the batch, retry later (bounded by queue length)
                    with self._lock:
                        self._q = batch + self._q
                    time.sleep(min(backoff, 30))
                    backoff *= 2
                    continue
                backoff = 1.0
            try:
                payload = "".join(json.dumps(e, default=str) + "\n" for e in batch).encode("utf-8")
                sock.sendall(payload)
            except OSError:
                try:
                    sock.close()
                finally:
                    sock = None
                with self._lock:
                    self._q = batch + self._q


_emitter: Optional[_Emitter] = None


def _emit(kind: str, **fields: Any) -> None:
    if _emitter is None:
        return
    _emitter.emit(
        {
            "v": PROTOCOL_VERSION,
            "id": uuid.uuid4().hex,
            "ts": time.time(),
            "kind": kind,
            "profile": _PROFILE,
            **fields,
        }
    )


# ----------------------------------------------------------------------------- capture / redaction


def _redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: m.group(0)[:4] + "…[redacted]", text)
    return text


def _capture(value: Any) -> Any:
    """Shape a tool arg/result for transport according to FLEETCONTROL_CAPTURE."""
    if _CAPTURE == "metadata":
        if isinstance(value, dict):
            return {"keys": sorted(value.keys())}
        return {"type": type(value).__name__, "len": len(str(value))}
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if _CAPTURE == "sanitized":
        text = _redact(text)
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + f"…[truncated {len(text) - _MAX_CHARS} chars]"
    return text


# ----------------------------------------------------------------------------- policy


class _Policy:
    """Policy file pushed by the daemon. Shape:
    {"version": 1, "profile": "...", "deny_tools": [...], "approve_tools": [...],
     "allow_tools": [...] | null, "rules": [{"id": "...", "tool": "regex", "action": "block|approve", "message": "..."}]}
    """

    def __init__(self, path: str):
        self.path = path
        self._mtime = 0.0
        self._data: dict = {}
        self._compiled: list[tuple[str, re.Pattern, str, str]] = []

    def _load(self) -> None:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            self._data, self._compiled, self._mtime = {}, [], 0.0
            return
        if st.st_mtime == self._mtime:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            self._compiled = [
                (r.get("id", "rule"), re.compile(r["tool"]), r.get("action", "block"), r.get("message", ""))
                for r in self._data.get("rules", [])
                if "tool" in r
            ]
            self._mtime = st.st_mtime
        except Exception as exc:  # unreadable policy: keep the last good one, flag it
            logger.warning("fleetcontrol: policy file unreadable (%s); keeping previous policy", exc)
            _emit("policy.unreadable", error=str(exc))

    def decide(self, tool_name: str) -> Optional[dict]:
        self._load()
        d = self._data
        if not d:
            return None
        allow = d.get("allow_tools")
        if allow is not None and tool_name not in allow:
            return {"action": "block", "message": f"Fleet Control policy: tool '{tool_name}' is not in this profile's allow-list.", "rule": "allow-list"}
        if tool_name in d.get("deny_tools", []):
            return {"action": "block", "message": f"Fleet Control policy: tool '{tool_name}' is denied for this profile.", "rule": "deny-list"}
        if tool_name in d.get("approve_tools", []):
            return {"action": "approve", "message": f"Fleet Control policy: '{tool_name}' requires human approval.", "rule_key": f"fleetcontrol:{tool_name}", "rule": "approve-list"}
        for rid, pat, action, message in self._compiled:
            if pat.search(tool_name):
                out = {"action": action, "message": message or f"Fleet Control policy rule '{rid}'.", "rule": rid}
                if action == "approve":
                    out["rule_key"] = f"fleetcontrol:{rid}"
                return out
        return None


_policy: Optional[_Policy] = None


# ----------------------------------------------------------------------------- hook callbacks


def on_pre_tool_call(tool_name: str, args: dict, task_id: str = "", **kwargs) -> Optional[dict]:
    try:
        decision = _policy.decide(tool_name) if _policy else None
        _emit(
            "tool.pre",
            session_id=task_id,
            tool=tool_name,
            args=_capture(args),
            decision=(decision or {}).get("action"),
            rule=(decision or {}).get("rule"),
        )
        if decision:
            # Return only the keys Hermes documents; 'rule' is ours.
            return {k: v for k, v in decision.items() if k in ("action", "message", "rule_key")}
    except Exception as exc:
        logger.debug("fleetcontrol pre_tool_call failed: %s", exc)
    return None


def on_post_tool_call(tool_name: str, args: dict, result: str, task_id: str = "", duration_ms: int = 0, **kwargs) -> None:
    try:
        is_error = False
        try:
            parsed = json.loads(result) if isinstance(result, str) else result
            is_error = isinstance(parsed, dict) and "error" in parsed
        except Exception:
            pass
        _emit(
            "tool.post",
            session_id=task_id,
            tool=tool_name,
            args=_capture(args),
            result=_capture(result),
            result_bytes=len(result) if isinstance(result, str) else None,
            duration_ms=duration_ms,
            error=is_error,
        )
    except Exception as exc:
        logger.debug("fleetcontrol post_tool_call failed: %s", exc)


def on_subagent_start(parent_session_id=None, parent_turn_id="", parent_subagent_id=None, child_session_id=None, child_subagent_id="", child_role="", child_goal="", **kwargs) -> None:
    _emit(
        "subagent.start",
        parent_session_id=parent_session_id,
        parent_turn_id=parent_turn_id,
        parent_subagent_id=parent_subagent_id,
        child_session_id=child_session_id,
        child_subagent_id=child_subagent_id,
        child_role=child_role,
        child_goal=_capture(child_goal),
    )


def on_subagent_stop(parent_session_id="", child_role=None, child_summary=None, child_status="", tool_call_history=None, duration_ms=0, **kwargs) -> None:
    _emit(
        "subagent.stop",
        parent_session_id=parent_session_id,
        child_session_id=kwargs.get("child_session_id"),
        child_role=child_role,
        child_status=child_status,
        child_summary=_capture(child_summary or ""),
        tool_call_history=tool_call_history or [],
        duration_ms=duration_ms,
    )


def on_session_start(session_id: str = "", **kwargs) -> None:
    _emit("session.start", session_id=session_id, platform=kwargs.get("platform"), model=kwargs.get("model"))


def on_session_end(session_id: str = "", **kwargs) -> None:
    _emit("session.end", session_id=session_id, completed=kwargs.get("completed"), interrupted=kwargs.get("interrupted"))


def on_pre_approval_request(**kwargs) -> None:
    _emit("approval.request", **{k: _capture(v) for k, v in kwargs.items() if k in ("tool_name", "session_id", "rule_key", "message")})


def on_post_approval_response(**kwargs) -> None:
    _emit("approval.response", **{k: v for k, v in kwargs.items() if k in ("tool_name", "session_id", "rule_key", "approved", "decision")})


# ----------------------------------------------------------------------------- registration


def register(ctx) -> None:
    global _emitter, _policy
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    _policy = _Policy(os.path.join(home, "fleetcontrol", "policy.json"))
    _emitter = _Emitter(_SOCKET or _default_socket_path())

    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("subagent_start", on_subagent_start)
    ctx.register_hook("subagent_stop", on_subagent_stop)
    ctx.register_hook("on_session_start", on_session_start)
    ctx.register_hook("on_session_end", on_session_end)
    ctx.register_hook("pre_approval_request", on_pre_approval_request)
    ctx.register_hook("post_approval_response", on_post_approval_response)

    _emit("plugin.registered", plugin_version=PLUGIN_VERSION, capture=_CAPTURE)
    logger.info("fleetcontrol plugin %s registered (capture=%s, socket=%s)", PLUGIN_VERSION, _CAPTURE, _emitter.target)
