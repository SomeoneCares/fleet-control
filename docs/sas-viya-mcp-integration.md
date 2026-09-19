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

Then authorize, then test:

```bash
HERMES_PROFILE=<profile> hermes mcp login sas-viya --flow browser
curl -X POST "http://127.0.0.1:9129/api/mcp/servers/sas-viya/test?profile=<profile>" -H "X-Hermes-Session-Token: $TOKEN"
```

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

## What Fleet Control shows

After `Discover now` on Integrations, `sas-viya` appears on `hermesbo-lab-01` with all six
profiles, all 51 tools enumerated, `AUTH: oauth`, and:

- **Health: Unreachable** — with the real reason printed (5 of 6 profiles hold no token)
- **Used by: None**, every tool marked *"No one"* — no blueprint declares `mcps: [sas-viya]` yet

Both are correct: the instance owns the connection, the blueprint owns the permission.
