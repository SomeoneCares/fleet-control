# SAS Viya MCP Server on a Hermes host

**Status: working end to end, 2026-09-20.** A Hermes profile on `hermesbo-lab-01` calls SAS Viya
analytics through SAS's own MCP server, and Fleet Control discovers it and lists its tools.

This file exists because almost none of it was guessable. Four traps cost most of the time.

## Topology

| Piece | Where |
|---|---|
| SAS Viya + SAS MCP Server | `viya@69.30.204.121`, published as `https://viya.internal` (RKE2, Contour) |
| MCP endpoint | `https://viya.internal/sas-mcp/mcp` — note the `/mcp`; the path-prefix root is not a route |
| Tool surface | `readOnly: true`, all tiers: **51 of 92 tools**, no `execute_sas_code` |
| Hermes host | `hermes@192.168.100.178` (HermesBO), instance `hermesbo-lab-01` |

The MCP server is configured **on the Hermes host**, never in Fleet Control. The blueprint only
says which agents may use it (`mcps: [sas-viya]`). No credential passes through Fleet Control.

## Registering it

Per profile, against the agent's loopback dashboard (session token in `~/.fleetctl-agent/dashboard.env`):

```bash
TOKEN=$(sed -n 's/^HERMES_DASHBOARD_SESSION_TOKEN=//p' ~/.fleetctl-agent/dashboard.env | tail -1)
curl -X POST "http://127.0.0.1:9129/api/mcp/servers?profile=<profile>" \
  -H "X-Hermes-Session-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"sas-viya","url":"https://viya.internal/sas-mcp/mcp","auth":"oauth"}'
```

Then authorize, **restart the gateway**, then test:

```bash
hermes -p <profile> mcp login sas-viya --flow browser   # -p, before the subcommand
hermes gateway restart                                   # or the agent keeps the old credential
curl -X POST "http://127.0.0.1:9129/api/mcp/servers/sas-viya/test?profile=<profile>" -H "X-Hermes-Session-Token: $TOKEN"
```

Both of those lines were wrong in the first version of this file, and each cost an hour:

* **`HERMES_PROFILE=… hermes mcp login` does not work.** The env var is ignored; the login writes to
  whichever profile is current (`◆` in `hermes profile list`), so it silently re-authorises `default`
  while the profile you meant still reports no cached tokens. The documented selector is a global
  flag **before** the subcommand: `hermes -p <profile> mcp login …` (flag → env var → sticky default).
* **The restart is not optional.** See Trap 5.

## Trap 1 — TLS: the server sends only its leaf certificate

`viya.internal` presents `CN=sas-viya-openssl-ingress-certificate`, issued by
`CN=SAS Viya openssl Root CA Certificate`, and **does not send the root**. Saving the leaf is
useless: `openssl x509` takes only the first certificate in the chain, and OpenSSL still cannot
build a path. Get the real root from the cluster:

```bash
ssh viya@69.30.204.121 'kubectl -n viya get secret sas-viya-ca-certificate-secret -o jsonpath="{.data.ca\.crt}" | base64 -d'
```

(Subject == issuer confirms it is the self-signed root.)

**Hermes ignores the system trust store.** `update-ca-certificates` fixes `curl` and proves
nothing about Hermes. The CA has to go into the `certifi` bundle inside the Hermes venv:

```
~/.hermes/hermes-agent/venv/lib/python3.11/site-packages/certifi/cacert.pem
```

**And `SSL_CERT_FILE` must point at that same bundle.** Setting it to the system bundle
(`/etc/ssl/certs/ca-certificates.crt`) actively breaks things: it overrides certifi with a store
that lacks the root. That mistake cost an hour.

Both `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` are set in `~/.fleetctl-agent/dashboard.env`.

> **Fragile:** a `certifi` upgrade wipes the appended CA. This belongs in the host build, not in a
> one-off command. A backup of the original bundle is kept at `cacert.pem.orig`.

## Trap 2 — device flow is not available; browser PKCE is

`hermes mcp login sas-viya --flow device` fails with *"Server does not advertise device
authorization"*. Use `--flow browser`. **Dynamic Client Registration works**, so no SASLogon
client has to be registered by hand — Hermes registers itself and receives a `client_id`.

The endpoint advertises its own discovery document:

```
www-authenticate: Bearer resource_metadata="https://viya.internal/.well-known/oauth-protected-resource/sas-mcp/mcp"
```

## Trap 3 — the OAuth callback over SSH

The login listens on `127.0.0.1:27890/callback` **on the Hermes host**, while the browser is on
a workstation. Forward the port before approving:

```bash
ssh -N -L 27890:127.0.0.1:27890 hermes@192.168.100.178
```

**The login process must keep a live stdin.** Started with `< /dev/null` it hits EOF at its paste
prompt, retries, and the retry dies with *"port 27890 already in use"* — killing its own listener,
so the approval lands nowhere. Run it interactively, or give it a FIFO:

```bash
mkfifo /tmp/mcp-login.in
(setsid sh -c 'exec sleep 3600 > /tmp/mcp-login.in' &)
(setsid hermes mcp login sas-viya --flow browser < /tmp/mcp-login.in > /tmp/mcp-login.log 2>&1 &)
```

Hermes also offers a paste fallback: approve, copy the `?code=...&state=...` from the address bar,
paste it at the prompt. That avoids the port forward entirely.

## Trap 4 — registration and tokens are per profile

Profiles are isolated Hermes instances. `?profile=default` writes to `~/.hermes/config.yaml`;
**named profiles do not inherit it** and report `Server 'sas-viya' not found`. Each profile needs
its own registration *and* its own `hermes mcp login`.

**Consequence for fleet design:** a fleet of N SAS-using agents needs N browser consents, each
expiring independently. The alternative is `allowRawBearer: true` on the SAS chart plus
`auth: "header"` with a service-account token — one credential, no consent, but every agent then
shares one identity in SAS's audit trail. Per-profile OAuth is more work and better evidence.

Token refresh across a restart is **not yet verified**.

## Trap 5 — a running gateway keeps serving the credential it started with

`hermes mcp login` writes the token to disk. **The running gateway does not pick it up.** It keeps
using the credential it had, fails OAuth, and parks the server:

```
MCP server 'sas-viya' hit a permanent error, parking without retries;
will self-probe every 300s (state: connected → parked): OAuthNonInteractiveError
```

There is no `reload` — `hermes gateway --help` offers only `restart`. So after any login:

```bash
hermes gateway restart
```

This is easy to miss because `/api/mcp/servers/sas-viya/test` **passes anyway**: the dashboard opens
its own connection with the fresh token, while the gateway — the thing that actually runs agents —
is still parked. A green `/test` next to a hanging agent run is this trap.

The first version of this file missed it entirely: the original session happened to restart the
gateway for an unrelated reason, so the recipe appeared to work.

> **Cost for a fleet:** the restart drains in-flight turns and interrupts **every profile on the
> host**, not only the SAS-using ones. It waits up to `agent.restart_after_turn_timeout` (1815s by
> default) for in-flight work; a hung run blocks it until you kill the old process.

## Recovering after a SAS outage

Credentials do **not** survive the MCP server going away. After Viya returned from an outage, every
profile reported `no cached tokens found`, and each `hermes mcp login` produced a **new `client_id`**
rather than reusing the stored registration — so the server appears to discard dynamic client
registrations when its pod restarts.

Recovery is therefore, per SAS-using profile:

1. `hermes -p <profile> mcp login sas-viya --flow browser` — a human approves in a browser
2. `hermes gateway restart` — once, after the last login
3. `/test` each profile to confirm

**For a fleet that means N browser consents plus a gateway restart after any SAS maintenance.** If
that is unacceptable, the alternatives are to persist the MCP server's OAuth store, or to set
`allowRawBearer: true` and use `auth: "header"` with a service account — which trades away per-agent
attribution in SAS's audit trail, since every agent then presents one identity.

## When it breaks: telling the failures apart

Hermes' `/test` returns an **empty** `error` string for a dead upstream. When it does, the Fleet
Control Agent checks the url itself (name → TCP → TLS → HTTP, no credentials) and reports which layer
failed, so a blank no longer reads like a credential problem. To check by hand from the Hermes host:

| Symptom | Meaning |
| --- | --- |
| TCP connects, TLS completes, HTTP `000` | The ingress is up, the workload behind it is dead. Not a credential problem. |
| HTTP `401` | The server is healthy and asking for authorization — this is the good case. |
| `CERTIFICATE_VERIFY_FAILED` | Trap 1: the CA is missing from the bundle Hermes actually uses. |
| `no cached tokens found` | That profile has never been authorised, or its token lapsed. |
| `OAuthNonInteractiveError` on a *connected* server | Trap 5: the gateway is serving a stale credential. Restart it. |
| `/test` passes but agent runs hang | Trap 5 again — dashboard fresh, gateway parked. |

```bash
curl -s -o /dev/null -w "%{http_code} connect=%{time_connect}s tls=%{time_appconnect}s\n" \
  https://viya.internal/sas-mcp/mcp
```

Registration and tokens are per profile, so Integrations shows health per profile: a server that some
profiles reach and others do not is `Degraded`, and the detail lists which profile fails and why.
Deregister the server from profiles no blueprint grants it to; the next discovery drops them from the list:

```bash
curl -X DELETE "http://127.0.0.1:9129/api/mcp/servers/sas-viya?profile=<profile>" -H "X-Hermes-Session-Token: $TOKEN"
```

## The host needs disk, and fails quietly without it

A full root filesystem breaks Hermes in ways that look like anything but a disk problem: OAuth flows
die mid-handshake with `OSError: [Errno 28] No space left on device` thrown from inside Hermes's own
logging, agent runs hang with no error, and **session transcripts stop being written — which is what
Assurance reads as evidence**. Check the disk before diagnosing anything subtle:

```bash
df -h /
```

On the lab host the culprit was not Hermes (~4 GB) but a nightly backup of `/srv/bid-office` that
included its own `backups/` directory: 193 MB → 294 MB → 1.2 GB → 2.3 GB → 8 GB → 13 GB → 24 GB →
58 GB on consecutive nights. Any backup on a Hermes host must exclude its own output directory.

## What Fleet Control shows

After `Discover now` on Integrations, `sas-viya` appears on `hermesbo-lab-01` with all six
profiles, all 51 tools enumerated, `AUTH: oauth`, and:

- **Health** per profile — `Degraded` while some profiles hold no token, with the reason for each
- **Used by** — the agents whose blueprint's *applied* version declares `mcps: [sas-viya]`; a newer
  draft that adds one shows as "once … is applied (draft)", not as a user

Both are correct: the instance owns the connection, the blueprint owns the permission.
