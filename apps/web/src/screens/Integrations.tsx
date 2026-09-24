import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api, type Integration, type IntegrationKind } from "../api/client";
import { useAuth } from "../lib/auth";
import { errorText, useLoad, useNow } from "../lib/hooks";
import { HEALTH_LABEL, HEALTH_TONE, KIND_LABEL, allowedAgents, discoveryState, distinctAgents, matchesQuery, toolRules } from "../lib/integrations";
import { timeAgo, type Tone } from "../lib/view";
import { Banner, Button, Card, Chip, Field, INPUT, Icon, Modal, Mono, PageHeader, Spinner } from "../components/ui";

// design/screens/Integrations: table · detail rail
export function IntegrationsScreen() {
  const { can } = useAuth();
  const now = useNow();
  const { data, error, reload } = useLoad(api.integrations, [], 10_000);
  const { data: instances } = useLoad(api.instances, []);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<IntegrationKind | "">("");
  const [selected, setSelected] = useState<string | null>(null);
  const [registering, setRegistering] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: Tone; text: string } | null>(null);

  const rows = (data?.integrations ?? []).filter((r) => (!kind || r.kind === kind) && matchesQuery(r, query));
  const current = rows.find((r) => `${r.kind}:${r.name}` === selected) ?? rows[0] ?? null;
  const discovery = discoveryState(data ?? null);

  async function act(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      setMsg({ tone: "info", text: done });
      await reload();
    } catch (e) {
      setMsg({ tone: "error", text: errorText(e) });
    } finally {
      setBusy(false);
    }
  }

  const discoverAll = () => act(async () => {
    for (const id of discovery.canDiscover) await api.discoverIntegrations(id);
  }, `Discovery queued on ${discovery.canDiscover.length} instance(s); the agents answer on their next poll.`);

  return (
    <>
      <PageHeader crumb="Estate" title="Integrations"
        subtitle="MCP servers and model providers discovered on your instances, with who may use them."
        actions={<>
          {can("instances.operate") && (
            <Button icon="refresh" disabled={busy || discovery.canDiscover.length === 0} onClick={() => void discoverAll()}
              title={discovery.canDiscover.length ? undefined : "Needs an instance with a paired agent and imported profiles"}>
              {busy && <Spinner />}Discover now
            </Button>
          )}
          {can("instances.operate") && <Button variant="primary" icon="plus" onClick={() => setRegistering(true)}>Register MCP server</Button>}
        </>} />

      {msg && <Banner tone={msg.tone} className="mb-4">{msg.text}</Banner>}
      {discovery.never.length > 0 && (
        <Banner tone="info" className="mb-4">
          Never probed: {discovery.never.join(", ")}. Discovery connects to each MCP server to list its tools; until then health is unknown.
        </Banner>
      )}

      <div className="flex gap-5 items-start">
        <Card className="flex-1 min-w-0 overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-3 border-b border-hairline">
            <input className={`${INPUT} h-8 max-w-[240px]`} placeholder="Filter integrations" value={query} onChange={(e) => setQuery(e.target.value)} />
            <select aria-label="Type" className={`${INPUT} h-8 w-[180px]`} value={kind} onChange={(e) => setKind(e.target.value as IntegrationKind | "")}>
              <option value="">Type: All</option>
              <option value="mcp">MCP servers</option>
              <option value="model">Model providers</option>
            </select>
            <div className="flex-1" />
            <span className="text-small text-text-secondary">{discovery.at ? `Last discovery ${timeAgo(discovery.at, now)}` : "No discovery yet"}</span>
          </div>
          <div className="grid grid-cols-[2.2fr_1.1fr_1.2fr_0.8fr_1.1fr] gap-3 px-4 py-2.5 border-b border-hairline text-label uppercase text-text-secondary">
            <div>Integration</div><div>Type</div><div>Instances</div><div>Used by</div><div>Health</div>
          </div>
          {error && <Banner tone="error" className="m-4">{error}</Banner>}
          {!data && !error && <div className="px-4 py-4 flex gap-2 items-center text-text-secondary"><Spinner /> Loading integrations…</div>}
          {data && rows.length === 0 && (
            <p className="m-0 px-4 py-6 text-[13px] text-text-secondary">
              {data.integrations.length === 0
                ? <>Nothing discovered yet. Import live profiles on <Link to="/instances">Instances</Link>, then run a discovery.</>
                : "No integration matches the filters."}
            </p>
          )}
          {rows.map((r) => (
            <button key={`${r.kind}:${r.name}`} type="button" onClick={() => setSelected(`${r.kind}:${r.name}`)}
              className={`w-full text-left grid grid-cols-[2.2fr_1.1fr_1.2fr_0.8fr_1.1fr] gap-3 px-4 py-3.5 border-b border-hairline items-center cursor-pointer ${current === r ? "bg-container-low" : "hover:bg-surface"}`}>
              <span className="flex flex-col gap-0.5 min-w-0">
                <span className="font-semibold truncate">{r.name}</span>
                <Mono className="text-[12px] text-text-secondary truncate">{r.kind === "mcp" ? r.endpoint ?? r.transport ?? "configured on the instance" : r.models.join(", ") || "no model seen"}</Mono>
              </span>
              <span className="text-[13px]">{KIND_LABEL[r.kind]}</span>
              <span className="text-[13px] truncate" title={r.instances.join(", ")}>{r.environments.join(", ") || r.instances.join(", ")}</span>
              <span className="text-[13px]" title={distinctAgents(r).map((u) => `${u.agent} (${u.blueprints.join(", ")})`).join("\n")}>
                {distinctAgents(r).length ? `${distinctAgents(r).length} agent${distinctAgents(r).length === 1 ? "" : "s"}` : <span className="text-outline">None</span>}
                {r.planned_by.length > 0 && <span className="block text-small text-text-secondary">+{r.planned_by.length} in a draft</span>}
              </span>
              <span className="flex flex-col gap-1 items-start">
                <Chip tone={HEALTH_TONE[r.health]}>{HEALTH_LABEL[r.health]}</Chip>
                {!r.enabled_everywhere && <span className="text-small text-text-secondary">disabled somewhere</span>}
              </span>
            </button>
          ))}
        </Card>

        {current && <DetailRail key={`${current.kind}:${current.name}`} row={current} instances={(instances ?? []).map((i) => i.id)} busy={busy}
          onChange={(fn, done) => void act(fn, done)} />}
      </div>

      {registering && <RegisterModal onClose={() => setRegistering(false)}
        onDone={(text) => { setRegistering(false); setMsg({ tone: "info", text }); void reload(); }} />}
    </>
  );
}

function DetailRail({ row, busy, onChange }: {
  row: Integration; instances: string[]; busy: boolean; onChange: (fn: () => Promise<unknown>, done: string) => void;
}) {
  const { can } = useAuth();
  const [profileRef, setProfileRef] = useState(row.profiles[0] ?? "");
  const [instance, profile] = (profileRef || "/").split("/");

  return (
    <Card className="w-[400px] shrink-0 p-[18px] flex flex-col gap-3.5">
      <div className="flex justify-between items-start gap-3">
        <div className="min-w-0">
          <div className="text-[15px] font-semibold truncate">{row.name}</div>
          <div className="text-small text-text-secondary">{KIND_LABEL[row.kind]}{row.transport ? ` · ${row.transport}` : ""}</div>
        </div>
        <Chip tone={HEALTH_TONE[row.health]}>{HEALTH_LABEL[row.health]}</Chip>
      </div>
      {row.error && <Banner tone="error" className="text-small">{row.error}</Banner>}

      <div className="border border-hairline rounded-control divide-y divide-hairline">
        {row.kind === "mcp" ? (
          <>
            <Detail label="Endpoint"><Mono className="break-all">{row.endpoint ?? "on the instance's configuration"}</Mono></Detail>
            <Detail label="Auth">{row.auth ?? "none declared"}</Detail>
          </>
        ) : (
          <Detail label="Models">{row.models.join(", ") || "none seen"}</Detail>
        )}
        <Detail label="Instances">{row.instances.join(", ") || "none"}</Detail>
        <Detail label="Profiles">
          {row.kind === "mcp" && row.profile_health.length > 0 ? row.profile_health.map((e) => (
            <div key={`${e.instance}/${e.profile}`} className="flex items-center justify-between gap-2 py-0.5" title={e.error ?? undefined}>
              <Mono className="text-[12px] truncate">{e.instance}/{e.profile}</Mono>
              <Chip tone={HEALTH_TONE[e.health]}>{HEALTH_LABEL[e.health]}</Chip>
            </div>
          )) : row.profiles.map((p) => <div key={p}><Mono className="text-[12px]">{p}</Mono></div>)}
        </Detail>
        <Detail label="Used by">
          {distinctAgents(row).length ? distinctAgents(row).map((u) => <div key={u.agent}>{u.agent} <span className="text-text-secondary">({u.blueprints.join(", ")})</span></div>)
            : <span className="text-text-secondary">No applied blueprint</span>}
          {row.planned_by.map((u) => (
            <div key={`${u.blueprint}:${u.agent}`} className="text-text-secondary">{u.agent} once {u.blueprint} v{u.version} is applied (draft)</div>
          ))}
        </Detail>
      </div>

      {row.kind === "mcp" && (
        <div className="flex flex-col gap-2">
          <div className="text-label uppercase text-text-secondary">Tools exposed</div>
          {row.tools.length === 0 ? (
            <p className="m-0 text-small text-text-secondary">
              {row.health === "unknown" ? "Not probed yet: run a discovery to list this server's tools." : "The server exposed no tools."}
            </p>
          ) : (
            <div className="border border-hairline rounded-control divide-y divide-hairline max-h-[260px] overflow-auto">
              {row.tools.map((t) => {
                const full = `${row.name}.${t.name}`;
                const { blockedFor, approvalFor } = toolRules(row, full);
                const allowed = allowedAgents(row, full);
                return (
                  <div key={t.name} className="grid grid-cols-[124px_minmax(0,1fr)] gap-2.5 px-3 py-2.5 items-start">
                    <Mono className="text-[12px]">{t.name}</Mono>
                    <div className="flex flex-col gap-1 min-w-0">
                      {allowed.length ? (
                        <span className="flex flex-wrap gap-1.5">
                          {allowed.map((a) => <span key={a} className="inline-flex items-center h-[22px] px-2 rounded-full bg-secondary-tint text-secondary text-[12px] font-medium">{a}</span>)}
                        </span>
                      ) : <span className="text-small text-text-secondary">No one</span>}
                      {approvalFor.length > 0 && <span className="text-small text-warning">Needs human approval for {approvalFor.join(", ")}</span>}
                      {blockedFor.length > 0 && <span className="text-small text-text-secondary flex items-start gap-1"><Icon name="lock" size={12} className="mt-0.5 shrink-0" />Blocked by policy for {blockedFor.join(", ")}</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          <p className="m-0 text-small text-text-secondary">
            Which agents may use a server is part of the blueprint (their <Mono>mcps</Mono> and the policies), so it changes through a plan.
            The server's own configuration lives on the instance.
          </p>
        </div>
      )}

      {row.kind === "mcp" && can("instances.operate") && row.profiles.length > 0 && (
        <div className="flex flex-col gap-2 border-t border-hairline pt-3.5">
          <Field label="Change it on">
            <select className={INPUT} value={profileRef} onChange={(e) => setProfileRef(e.target.value)}>
              {row.profiles.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </Field>
          <div className="flex gap-2">
            <Button disabled={busy || !profile} onClick={() => onChange(() => api.changeMcpServer(row.name, { instance_id: instance, profile, enabled: !row.enabled_everywhere }),
              `${row.enabled_everywhere ? "Disabling" : "Enabling"} ${row.name} on ${profileRef}; the agent applies it on its next poll.`)}>
              {row.enabled_everywhere ? "Disable" : "Enable"}
            </Button>
            <Button variant="danger" disabled={busy || !profile}
              onClick={() => window.confirm(`Remove ${row.name} from ${profileRef}? Agents on that profile lose its tools.`)
                && onChange(() => api.changeMcpServer(row.name, { instance_id: instance, profile, remove: true }), `Removing ${row.name} from ${profileRef}.`)}>
              Remove
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 px-3 py-2.5">
      <span className="text-label uppercase text-text-secondary">{label}</span>
      <span className="text-[13px]">{children}</span>
    </div>
  );
}

function RegisterModal({ onClose, onDone }: { onClose: () => void; onDone: (text: string) => void }) {
  const { data: instances } = useLoad(api.instances, []);
  const usable = (instances ?? []).filter((i) => i.mode === "agent" && i.agent_version && i.live_profile_count > 0);
  const [instance, setInstance] = useState("");
  const chosen = usable.find((i) => i.id === instance) ?? usable[0] ?? null;
  const { data: detail } = useLoad(() => (chosen ? api.instance(chosen.id) : Promise.resolve(null)), [chosen?.id]);
  const [profile, setProfile] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"url" | "command">("url");
  const [endpoint, setEndpoint] = useState("");
  const [args, setArgs] = useState("");
  const [auth, setAuth] = useState<"none" | "oauth">("none");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const profiles = detail?.live_profiles ?? [];
  const useProfile = profiles.includes(profile) ? profile : profiles[0] ?? "";

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!chosen || !useProfile) return;
    setBusy(true);
    setError(null);
    try {
      await api.addMcpServer({
        instance_id: chosen.id, profile: useProfile, name: name.trim(), auth,
        ...(kind === "url" ? { url: endpoint.trim() } : { command: endpoint.trim(), args: args.split(" ").filter(Boolean) }),
      });
      onDone(`Adding ${name.trim()} to ${chosen.id}/${useProfile}; the agent writes it on its next poll, then re-discovers.`);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <Modal width={560} title="Register an MCP server" onClose={onClose}
      subtitle="Added to one profile on one instance through its Fleet Control Agent."
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="mcp-form" disabled={busy || !chosen || !useProfile || !name.trim() || !endpoint.trim()}>
          {busy && <Spinner />}Add server
        </Button>
      </>}>
      {usable.length === 0 ? (
        <Banner tone="warning">No instance with a paired Fleet Control Agent and imported profiles yet.</Banner>
      ) : (
        <form id="mcp-form" onSubmit={(e) => void submit(e)}>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Instance">
              <select className={INPUT} value={chosen?.id ?? ""} onChange={(e) => { setInstance(e.target.value); setProfile(""); }}>
                {usable.map((i) => <option key={i.id} value={i.id}>{i.id} ({i.environment})</option>)}
              </select>
            </Field>
            <Field label="Profile">
              <select className={INPUT} value={useProfile} onChange={(e) => setProfile(e.target.value)}>
                {profiles.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </Field>
          </div>
          <Field label="Name" hint="As agents will refer to it in the blueprint.">
            <input className={INPUT} autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="opensanctions" />
          </Field>
          <Field label="How it runs">
            <select className={INPUT} value={kind} onChange={(e) => setKind(e.target.value as "url" | "command")}>
              <option value="url">Remote server (URL)</option>
              <option value="command">Local server (command)</option>
            </select>
          </Field>
          <Field label={kind === "url" ? "URL" : "Command"}>
            <input className={INPUT} value={endpoint} onChange={(e) => setEndpoint(e.target.value)}
              placeholder={kind === "url" ? "https://server.example/mcp" : "npx"} />
          </Field>
          {kind === "command" && (
            <Field label="Arguments" hint="Separated by spaces.">
              <input className={INPUT} value={args} onChange={(e) => setArgs(e.target.value)} placeholder="-y @scope/mcp-server" />
            </Field>
          )}
          <Field label="Authentication">
            <select className={INPUT} value={auth} onChange={(e) => setAuth(e.target.value as "none" | "oauth")}>
              <option value="none">None</option>
              <option value="oauth">OAuth (authorise on the instance)</option>
            </select>
          </Field>
          <Banner tone="info" className="text-small">
            Credentials are never sent through Fleet Control: add API keys or tokens on the instance itself. Nothing here is stored as a secret.
          </Banner>
          {error && <Banner tone="error">{error}</Banner>}
        </form>
      )}
    </Modal>
  );
}
