# Hermes Agent 0.21.2 — control-plane API spike probes

Date: 2026-09-11 · Repo: `/home/claude/hermes-agent` · Python 3.11.15 · venv: `/home/claude/hermes-agent/.venv`
Time-boxed to ~20 min. **No repository source files were modified.** All state written under
`HERMES_HOME=/tmp/claude-0/-home-claude/5cacc100-007b-5559-94ce-6a996026db94/scratchpad/hermes-home`
(the env var is honoured — `hermes_constants.py` reads `HERMES_HOME`; the scratch home got `state.db`,
`response_store.db`, `runs_idempotency.db`, `sessions/`, `skills/`, `logs/`, etc.; nothing was written to `~`).

## TL;DR

| Item | Result |
|---|---|
| `pip install -e .` (full deps) | **FAILED** — sandbox egress policy blocks PyPI (`x-deny-reason: host_not_allowed` for `pypi.org`, `files.pythonhosted.org`; also `registry.npmjs.org`, `github.com`, `raw.githubusercontent.com`, `codeload.github.com` all 403 from the proxy). Not routed around, per proxy README. |
| Fallback install | `python3 -m venv --system-site-packages .venv` + `pip install -e . --no-deps --no-build-isolation` → OK (setuptools 79 already present). Present on box: starlette 1.0.0, uvicorn 0.46.0, httpx, pydantic 2.13.3, pyyaml, jinja2, requests, python-dotenv. **Missing:** `openai`, `rich`, `aiohttp`, `fastapi`, `fire`, `tenacity`, `prompt_toolkit`, `croniter`, `ruamel.yaml`, `snowballstemmer`, `firecrawl-anydoc`. |
| `hermes` CLI | `hermes --version` works. **Every subcommand (incl. `hermes dashboard`, `hermes gateway`) dies at parser build:** `hermes_cli/secrets_cli.py:14 from rich.console import Console → ModuleNotFoundError: No module named 'rich'`. |
| API server (`gateway/platforms/api_server.py`, aiohttp, default `127.0.0.1:8642`) | **Could not bind a real socket** (needs `aiohttp`). BUT the module imports cleanly (aiohttp is optional at import time), so I instantiated the real `APIServerAdapter` in-process with a ~40-line `aiohttp.web` stub (`json_response`/`Response` only) and invoked the **real handler methods** — the JSON bodies below are produced by unmodified Hermes code, but were *not* captured over HTTP with curl. |
| Dashboard backend (`hermes_cli/web_server.py`, FastAPI+uvicorn, default port **9119**) | **Could not start at all**: `import hermes_cli.web_server` → `Web UI requires fastapi and uvicorn. Install with: .venv/bin/python -m pip install 'fastapi' 'uvicorn[standard]'`. No dashboard responses captured; routes/auth documented from source only. |
| Write on dashboard API | **Not attempted** (server never started). Route + auth documented below. |
| `POST /v1/runs` w/o model key | Handler **accepts with 202** `{run_id, status:"started"}`, then the background run fails with `run.failed` / `"Failed to initialize OpenAI client: No module named 'openai'"` (would be a provider auth error with a fake key once `openai` is installed). |
| Real bug found | `GET /v1/skills` **always 500s** in this tree: `api_server.py:2643` calls `_find_all_skills(skip_disabled=False, include_editorial=True)` but `tools/skills_tool.py:187` is `def _find_all_skills(*, skip_disabled: bool = False)` → `TypeError: unexpected keyword argument 'include_editorial'`. |

## 1. How the servers are launched (from source)

- **API server** is not a standalone command; it is a *gateway platform adapter*: `hermes gateway` → `gateway/run.py` → `APIServerAdapter` (`gateway/platforms/api_server.py`). Enabled when `API_SERVER_KEY` (or `gateway.platforms.api_server.key` in config) is set and ≥16 chars (`gateway/config.py:498-520 _has_usable_api_server_key`). Env: `API_SERVER_HOST` (default `127.0.0.1`), `API_SERVER_PORT` (default `8642`), `API_SERVER_KEY`, `API_SERVER_CORS_ORIGINS`, `API_SERVER_MODEL_NAME`; config extra `model_routes`, `direct_model_requests`, `max_concurrent_runs`. Refuses to start without a usable key ("connect() refuses to start without API_SERVER_KEY", api_server.py:1364).
- **Auth**: `Authorization: Bearer <API_SERVER_KEY>` on every route except `GET /health` / `GET /v1/health` (`_require_auth` decorator, api_server.py:940). Profile-scoped path prefix `/p/<profile>/...` exists; named profiles fail closed without a profile-scoped key.
- **Dashboard** = `hermes dashboard` (`hermes_cli/main_dashboard.py` → `hermes_cli/web_server.py`), FastAPI app served by `uvicorn.Server`, default `port=9119` (`web_server.py:1370`), stale-pid parsing assumes `--host/--port` flags. Auth: a per-process session token `_SESSION_TOKEN = os.environ.get("HERMES_DASHBOARD_SESSION_TOKEN") or secrets.token_urlsafe(32)` (web_server.py:310-315), sent as header **`X-Hermes-Session-Token: <token>`** (legacy `Authorization: Bearer <token>` still accepted, web_server.py:395-406). The token is injected into the SPA HTML; an external control plane must set `HERMES_DASHBOARD_SESSION_TOKEN` at launch to know it. Loopback desktop-shell exemption exists (`_desktop_loopback_auth_exempt`). Only `/api/files/download` accepts `?token=`. The dashboard proxies to the gateway api_server for chat (docs/chronos-managed-cron-contract.md:138-146; dashboard returns 503 when api_server disabled).

Full API-server route table (from `APIServerAdapter._http_route_table()` at runtime):

```
route table:
  GET    /health
  GET    /health/detailed
  GET    /v1/health
  GET    /v1/models
  GET    /api/model/options
  GET    /v1/capabilities
  POST   /v1/browser-control/register
  GET    /v1/browser-control/ws
  POST   /v1/artifacts/upload
  GET    /v1/artifacts/download/{artifact_id}
  GET    /v1/skills
  GET    /v1/toolsets
  GET    /api/sessions
  POST   /api/sessions
  GET    /api/sessions/{session_id}
  PATCH  /api/sessions/{session_id}
  DELETE /api/sessions/{session_id}
  GET    /api/sessions/{session_id}/messages
  POST   /api/sessions/{session_id}/fork
  POST   /api/sessions/{session_id}/chat
  POST   /api/sessions/{session_id}/chat/stream
  POST   /api/sessions/{session_id}/model
  POST   /v1/chat/completions
  POST   /v1/responses
  GET    /v1/responses/{response_id}
  DELETE /v1/responses/{response_id}
  POST   /api/platforms/{platform}/events
  GET    /api/jobs
  POST   /api/jobs
  GET    /api/jobs/{job_id}
  PATCH  /api/jobs/{job_id}
  DELETE /api/jobs/{job_id}
  POST   /api/jobs/{job_id}/pause
  POST   /api/jobs/{job_id}/resume
  POST   /api/jobs/{job_id}/run
  POST   /v1/room-members/invitations
  GET    /v1/room-members/capabilities
  POST   /v1/room-members/grants/refresh
  POST   /v1/room-members/grants/revoke
  POST   /v1/runs
  GET    /v1/runs/{run_id}
  GET    /v1/runs/{run_id}/events
  POST   /v1/runs/{run_id}/approval
  POST   /v1/runs/{run_id}/steer
  POST   /v1/runs/{run_id}/stop
  POST   /api/cron/fire
```

Dashboard routes relevant to a control plane (static grep of `hermes_cli/web_server*.py`, `hermes_cli/web_routers/*.py`):

```
GET  /api/health          GET  /api/status
GET  /api/config          PUT  /api/config          GET /api/config/defaults   GET /api/config/schema
GET  /api/config/raw      PUT  /api/config/raw
GET  /api/profiles        POST /api/profiles        GET /api/profiles/active   POST /api/profiles/active
PATCH /api/profiles/{name}  DELETE /api/profiles/{name}  POST /api/profiles/import  POST /api/profiles/{name}/export
GET  /api/mcp/servers     POST /api/mcp/servers     DELETE /api/mcp/servers/{name}  POST /api/mcp/servers/{name}/test
GET  /api/mcp/catalog     POST /api/mcp/catalog/install
GET  /api/skills          GET  /api/skills/content
```

## 2. Startup attempts — verbatim failures

```
$ .venv/bin/pip install -e .
  ERROR: Could not find a version that satisfies the requirement setuptools==83.0.0 (from versions: none)
$ curl -sS -D - https://pypi.org/simple/setuptools/
  HTTP/2 403   x-deny-reason: host_not_allowed
$ NO_PROXY= curl https://files.pythonhosted.org/   → curl: (56) CONNECT tunnel failed, response 403
$ curl https://registry.npmjs.org/aiohttp → 403 ; https://github.com/... → 403 ; raw.githubusercontent.com → CONNECT 403

$ .venv/bin/hermes dashboard --help
  File "/home/claude/hermes-agent/hermes_cli/secrets_cli.py", line 14, in <module>
    from rich.console import Console
  ModuleNotFoundError: No module named 'rich'

$ .venv/bin/python -c "import hermes_cli.web_server"
  Web UI requires fastapi and uvicorn.
  Install with: /home/claude/hermes-agent/.venv/bin/python -m pip install 'fastapi' 'uvicorn[standard]'

$ .venv/bin/python -c "import gateway.platforms.api_server, gateway.run"   → OK (aiohttp import is try/except; AIOHTTP_AVAILABLE=False)
```

Runtime warnings emitted during handler probes (stderr): `Auxiliary: marking openrouter unhealthy for 60s (payment / credit error)`, `Auxiliary Nous client unavailable: no Nous authentication found (run: hermes auth)`, `state.db: linked SQLite 3.45.1 ... vulnerable to the WAL-reset corruption bug ... using journal_mode=DELETE`.

## 3. API-server handler responses (real handler code, stub transport)

Harness: `/tmp/claude-0/-home-claude/5cacc100-007b-5559-94ce-6a996026db94/scratchpad/harness.py`
(env: `API_SERVER_KEY=spike-fake-key-0123456789abcdef`, `OPENAI_API_KEY=sk-fake-not-a-real-key`; `PlatformConfig(extra={key, host:127.0.0.1, port:8642})`).
"headers": {} below means the handler set none — real aiohttp would add `Content-Type: application/json`.

### GET /health (no auth)
{
  "status": 200,
  "headers": {},
  "json": {
    "status": "ok",
    "platform": "hermes-agent",
    "version": "0.21.2"
  }
}

### GET /v1/capabilities WITHOUT bearer
{
  "status": 401,
  "headers": {},
  "json": {
    "error": {
      "message": "Invalid gateway API key (API_SERVER_KEY)",
      "type": "gateway_auth_error",
      "code": "gateway_auth_failed"
    }
  }
}

### GET /v1/capabilities (bearer)
{
  "status": 200,
  "headers": {},
  "json": {
    "object": "hermes.api_server.capabilities",
    "platform": "hermes-agent",
    "model": "hermes-agent",
    "auth": {
      "type": "bearer",
      "required": true
    },
    "runtime": {
      "mode": "server_agent",
      "tool_execution": "server",
      "split_runtime": false,
      "description": "The API server creates a server-side Hermes AIAgent; tools execute on the API-server host unless a future explicit split-runtime mode is enabled."
    },
    "features": {
      "chat_completions": true,
      "chat_completions_streaming": true,
      "responses_api": true,
      "responses_streaming": true,
      "run_submission": true,
      "runs_idempotency": {
        "supported": true,
        "durable": true,
        "retention_seconds": 86400
      },
      "run_status": true,
      "run_events_sse": true,
      "run_stop": true,
      "run_steer": true,
      "run_approval_response": true,
      "tool_progress_events": true,
      "approval_events": true,
      "session_resources": true,
      "model_options": true,
      "session_chat": true,
      "session_chat_streaming": true,
      "session_fork": true,
      "session_model_lock": true,
      "admin_config_rw": false,
      "jobs_admin": false,
      "memory_write_api": false,
      "skills_api": true,
      "audio_api": false,
      "realtime_voice": false,
      "session_continuity_header": "X-Hermes-Session-Id",
      "session_key_header": "X-Hermes-Session-Key",
      "cors": false,
      "browser_extension_control": {
        "enabled": false,
        "protocol_version": 1,
        "capabilities": [
          "browser_back",
          "browser_click",
          "browser_navigate",
          "browser_press",
          "browser_screenshot",
          "browser_scroll",
          "browser_snapshot",
          "browser_tab_activate",
          "browser_tabs",
          "browser_type",
          "controller.noop"
        ],
        "artifact_capabilities": [
          "browser_artifact_download",
          "browser_artifact_upload"
        ],
        "developer_capabilities": [
          "browser_cdp",
          "browser_evaluate"
        ],
        "developer_mode": false,
        "artifact_transport": {
          "upload": {
            "method": "POST",
            "path": "/v1/artifacts/upload"
          },
          "download": {
            "method": "GET",
            "path": "/v1/artifacts/download/{artifact_id}"
          },
          "max_bytes": 10485760,
          "ttl_seconds": 300.0,
          "allowed_mime_types": [
            "application/json",
            "application/pdf",
            "image/gif",
            "image/jpeg",
            "image/png",
            "image/webp",
            "text/plain"
          ]
        },
        "real_browser_actions": true,
        "transports": {
          "local_vps": "websocket-subprotocol-ticket",
          "cloud": "authenticated-gateway-rpc"
        }
      }
    },
    "endpoints": {
      "health": {
        "method": "GET",
        "path": "/health"
      },
      "health_detailed": {
        "method": "GET",
        "path": "/health/detailed"
      },
      "models": {
        "method": "GET",
        "path": "/v1/models"
      },
      "model_options": {
        "method": "GET",
        "path": "/api/model/options"
      },
      "chat_completions": {
        "method": "POST",
        "path": "/v1/chat/completions"
      },
      "responses": {
        "method": "POST",
        "path": "/v1/responses"
      },
      "runs": {
        "method": "POST",
        "path": "/v1/runs"
      },
      "run_status": {
        "method": "GET",
        "path": "/v1/runs/{run_id}"
      },
      "run_events": {
        "method": "GET",
        "path": "/v1/runs/{run_id}/events"
      },
      "run_approval": {
        "method": "POST",
        "path": "/v1/runs/{run_id}/approval"
      },
      "run_steer": {
        "method": "POST",
        "path": "/v1/runs/{run_id}/steer"
      },
      "run_stop": {
        "method": "POST",
        "path": "/v1/runs/{run_id}/stop"
      },
      "skills": {
        "method": "GET",
        "path": "/v1/skills"
      },
      "toolsets": {
        "method": "GET",
        "path": "/v1/toolsets"
      },
      "sessions": {
        "method": "GET",
        "path": "/api/sessions"
      },
      "session_create": {
        "method": "POST",
        "path": "/api/sessions"
      },
      "session": {
        "method": "GET",
        "path": "/api/sessions/{session_id}"
      },
      "session_update": {
        "method": "PATCH",
        "path": "/api/sessions/{session_id}"
      },
      "session_delete": {
        "method": "DELETE",
        "path": "/api/sessions/{session_id}"
      },
      "session_messages": {
        "method": "GET",
        "path": "/api/sessions/{session_id}/messages"
      },
      "session_fork": {
        "method": "POST",
        "path": "/api/sessions/{session_id}/fork"
      },
      "session_chat": {
        "method": "POST",
        "path": "/api/sessions/{session_id}/chat"
      },
      "session_chat_stream": {
        "method": "POST",
        "path": "/api/sessions/{session_id}/chat/stream"
      },
      "session_model_lock": {
        "method": "POST",
        "path": "/api/sessions/{session_id}/model"
      },
      "browser_control_register": {
        "method": "POST",
        "path": "/v1/browser-control/register"
      },
      "browser_control_ws": {
        "method": "GET",
        "path": "/v1/browser-control/ws"
      },
      "artifact_upload": {
        "method": "POST",
        "path": "/v1/artifacts/upload"
      },
      "artifact_download": {
        "method": "GET",
        "path": "/v1/artifacts/download/{artifact_id}"
      }
    }
  }
}

### GET /health/detailed (bearer)
{
  "status": 200,
  "headers": {},
  "json": {
    "status": "degraded",
    "readiness": {
      "status": "degraded",
      "checks": {
        "state_db": {
          "status": "ok"
        },
        "session_store": {
          "status": "ok"
        },
        "config": {
          "status": "ok",
          "detail": "using defaults"
        },
        "model": {
          "status": "degraded"
        },
        "disk": {
          "status": "ok",
          "used_percent": 4.8,
          "free_bytes": 31792918528
        },
        "gateway": {
          "status": "degraded",
          "state": "unknown",
          "connected_platforms": 0,
          "platforms": 0
        },
        "background_queues": {
          "status": "ok",
          "active_api_runs": 0,
          "process_completions": 0,
          "active_delegations": 0
        }
      }
    },
    "platform": "hermes-agent",
    "version": "0.21.2",
    "gateway_state": null,
    "platforms": {},
    "active_agents": 0,
    "gateway_busy": false,
    "gateway_drainable": false,
    "exit_reason": null,
    "updated_at": null,
    "pid": 7431
  }
}

### GET /v1/models (bearer)
{
  "status": 200,
  "headers": {},
  "json": {
    "object": "list",
    "data": [
      {
        "id": "hermes-agent",
        "object": "model",
        "created": 1789169800,
        "owned_by": "hermes",
        "permission": [],
        "root": "hermes-agent",
        "parent": null
      }
    ]
  }
}

### GET /v1/skills (bearer)
{
  "status": 500,
  "headers": {},
  "json": {
    "error": {
      "message": "Failed to enumerate skills",
      "type": "server_error",
      "param": null,
      "code": null
    }
  }
}
(cause: `TypeError: _find_all_skills() got an unexpected keyword argument 'include_editorial'` — see TL;DR)

### GET /v1/toolsets (bearer)
{
  "status": 200,
  "headers": {},
  "json": {
    "object": "list",
    "platform": "api_server",
    "data": [
      {
        "name": "web",
        "label": "\ud83d\udd0d Web Search & Scraping",
        "description": "web_search, web_extract",
        "enabled": true,
        "configured": true,
        "tools": [
          "web_extract",
          "web_search"
        ]
      },
      {
        "name": "browser",
        "label": "\ud83c\udf10 Browser Automation",
        "description": "navigate, click, type, scroll",
        "enabled": true,
        "configured": true,
        "tools": [
          "browser_back",
          "browser_cdp",
          "browser_click",
          "browser_console",
          "browser_dialog",
          "browser_exec",
          "browser_get_images",
          "browser_navigate",
          "browser_press",
          "browser_scroll",
          "browser_snapshot",
          "browser_type",
          "browser_vault_enter_code",
          "browser_vault_fill",
          "browser_vault_list",
          "browser_vault_save_login",
          "browser_vault_unlock",
          "browser_vision"
        ]
      },
      {
        "name": "terminal",
        "label": "\ud83d\udcbb Terminal & Processes",
        "description": "terminal, process",
        "enabled": true,
        "configured": true,
        "tools": [
          "process_manage",
          "terminal"
        ]
      },
      {
        "name": "file",
        "label": "\ud83d\udcc1 File Operations",
        "description": "read, write, patch, search",
        "enabled": true,
        "configured": true,
        "tools": [
          "patch",
          "read_file",
          "search_files",
          "write_file"
        ]
      },
      {
        "name": "code_execution",
        "label": "\u26a1 Code Execution",
        "description": "execute_code",
        "enabled": true,
        "configured": true,
        "tools": [
          "execute_code"
        ]
      },
      {
        "name": "vision",
        "label": "\ud83d\udc41\ufe0f  Vision / Image Analysis",
        "description": "vision_analyze",
        "enabled": true,
        "configured": false,
        "tools": [
          "vision_analyze"
        ]
      },
      {
        "name": "video",
        "label": "\ud83c\udfac Video Analysis",
        "description": "video_analyze (requires video-capable model)",
        "enabled": false,
        "configured": true,
        "tools": [
          "video_analyze"
        ]
      },
      {
        "name": "image_gen",
        "label": "
  ... (trimmed)

### POST /v1/runs (bearer, trivial prompt)
{
  "status": 202,
  "headers": {},
  "json": {
    "run_id": "run_3e7ae759900c4b35b9bdfb835dc33b49",
    "status": "started",
    "replayed": false
  }
}
Request body was `{"input": "Say hi in one word."}`. Optional headers honoured by the handler: `Idempotency-Key`, `X-Hermes-Session-Id`, `X-Hermes-Session-Key`. Event stream URL is `GET /v1/runs/{run_id}/events` (SSE), status is `GET /v1/runs/{run_id}`.

### queued SSE events for run_3e7ae759900c4b35b9bdfb835dc33b49
[
  {
    "event": "run.failed",
    "run_id": "run_3e7ae759900c4b35b9bdfb835dc33b49",
    "timestamp": 1789169800.8245838,
    "error": "Failed to initialize OpenAI client: No module named 'openai'"
  },
  null
]
(the trailing `null` is the stream-end sentinel)

### GET /v1/runs/run_3e7ae759900c4b35b9bdfb835dc33b49
{
  "status": 200,
  "headers": {},
  "json": {
    "object": "hermes.run",
    "run_id": "run_3e7ae759900c4b35b9bdfb835dc33b49",
    "status": "failed",
    "updated_at": 1789169800.8245716,
    "created_at": 1789169800.461099,
    "session_id": "run_3e7ae759900c4b35b9bdfb835dc33b49",
    "model": "hermes-agent",
    "error": "Failed to initialize OpenAI client: No module named 'openai'",
    "last_event": "run.failed"
  }
}

### GET /v1/runs/run_3e7ae759900c4b35b9bdfb835dc33b49/events
{
  "status": 200,
  "headers": {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no"
  }
}
(SSE body not captured — the stub `StreamResponse` does not drain the queue; the queued events are shown above.)

## 4. Dashboard write test

Not performed: the FastAPI app cannot be imported without `fastapi`. From source, the write would be
`POST /api/profiles` (body `{"name": ...}`, handler in `hermes_cli/web_server_profiles.py`) or `PUT /api/config`
(`hermes_cli/web_server_config.py`), both requiring `X-Hermes-Session-Token` (or `Authorization: Bearer <token>`),
and profile creation writes under `<hermes root>/profiles/<name>/` (`hermes_constants.py:147` root-vs-profile-home logic).

## 5. What is needed to redo this over real HTTP

Same steps, on a host with PyPI access: `python3 -m venv .venv && .venv/bin/pip install -e '.[web,messaging]'`
(`web` extra = fastapi+uvicorn; `aiohttp` comes from `messaging`/`slack`/`matrix` extras — `aiohttp==3.14.3`), then
`HERMES_HOME=<scratch> API_SERVER_KEY=<32 chars> OPENAI_API_KEY=fake hermes gateway` (api_server on 127.0.0.1:8642) and
`HERMES_HOME=<scratch> HERMES_DASHBOARD_SESSION_TOKEN=<token> hermes dashboard --host 127.0.0.1 --port 9119`, then curl with
`Authorization: Bearer $API_SERVER_KEY` / `X-Hermes-Session-Token: $token` respectively.
