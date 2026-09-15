import { useState } from "react";
import { useNavigate, useParams } from "react-router";
import { api, type Environment, type Instance } from "../api/client";
import { useAuth } from "../lib/auth";
import { errorText, useLoad, useNow } from "../lib/hooks";
import { ENV_LABEL, capabilityRows, formatDate, instanceStatus, timeAgo, type Tone } from "../lib/view";
import { Banner, Button, Card, Chip, INPUT, Icon, KpiTile, Label, Mono, PageHeader, Spinner } from "../components/ui";
import { ConnectDrawer } from "./ConnectDrawer";
import { DriftModal } from "./DriftModal";
import { ImportBlueprintModal } from "./ImportBlueprintModal";

export function InstancesScreen() {
  const { id: driftFor } = useParams();
  const navigate = useNavigate();
  const now = useNow();
  const { can } = useAuth();
  const { data: instances, error, loading, reload } = useLoad(api.instances, [], 5000);
  const [selected, setSelected] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [query, setQuery] = useState("");
  const [env, setEnv] = useState<"all" | Environment>("all");

  const list = instances ?? [];
  const shown = list.filter((i) => (env === "all" || i.environment === env) && i.id.includes(query.trim().toLowerCase()));
  const current = list.find((i) => i.id === (selected ?? driftFor)) ?? shown[0] ?? null;

  const header = (
    <PageHeader crumb="Estate" title="Instances" subtitle="Hermes installations Fleet Control can read from and apply blueprints to."
      actions={<>
        <Button icon="refresh" onClick={() => void reload()}>Re-check all</Button>
        {can("instances.connect") && <Button variant="primary" icon="plus" onClick={() => setConnecting(true)}>Connect instance</Button>}
      </>} />
  );
  const overlays = <>
    {connecting && <ConnectDrawer onClose={() => setConnecting(false)} onCreated={() => void reload()} />}
    {driftFor && <DriftModal instanceId={driftFor} onClose={() => { navigate("/instances"); void reload(); }} />}
  </>;

  if (loading && !instances) return <>{header}<div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading instances…</div></>;
  if (error && !instances) return <>{header}<Banner tone="error">Cannot reach the Fleet Control API: {error}. Is it running on port 8080?</Banner></>;

  if (list.length === 0) {
    return (
      <>
        {header}
        <Card className="max-w-[680px] mx-auto mt-10 px-10 py-12 text-center">
          <div className="mx-auto mb-5 size-14 rounded-card bg-primary-tint text-primary flex items-center justify-center"><Icon name="server" size={26} /></div>
          <h2 className="text-section m-0">No Hermes instances connected yet</h2>
          <p className="text-text-secondary mt-2 mb-6">Point Fleet Control at a running Hermes Agent and it will read what is already there before you change anything.</p>
          {can("instances.connect")
            ? <Button variant="primary" icon="plus" onClick={() => setConnecting(true)}>Connect instance</Button>
            : <p className="m-0 text-small text-text-secondary">Ask an Admin to connect one.</p>}
          <ol className="grid grid-cols-3 gap-4 text-left list-none p-0 mt-9 pt-6 border-t border-hairline">
            {["Install the Fleet Control Agent on the Hermes host", "Fleet Control discovers profiles, skills and MCPs", "Import what is running, or apply a blueprint"].map((s, n) => (
              <li key={s} className="flex gap-2.5 text-small text-text-secondary">
                <span className="size-5 shrink-0 rounded-full bg-primary-tint text-primary text-[11px] font-bold flex items-center justify-center">{n + 1}</span>{s}
              </li>
            ))}
          </ol>
        </Card>
        {overlays}
      </>
    );
  }

  const fresh = list.filter((i) => i.mode === "agent" && i.last_heartbeat && now - i.last_heartbeat <= 90);
  const drifting = list.filter((i) => i.open_drift > 0);
  const versions = Object.entries(list.reduce<Record<string, number>>((acc, i) => {
    if (i.hermes_version) acc[i.hermes_version] = (acc[i.hermes_version] ?? 0) + 1;
    return acc;
  }, {}));
  const profiles = list.reduce((n, i) => n + i.live_profile_count, 0);

  return (
    <>
      {header}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <KpiTile label="Connected" value={list.length} unit={list.length === 1 ? "instance" : "instances"}
          note={`${fresh.length} agent${fresh.length === 1 ? "" : "s"} reporting`} tone={fresh.length ? "success" : "neutral"} />
        <KpiTile label="Profiles imported" value={profiles} unit="live profiles"
          note={profiles ? `across ${list.filter((i) => i.live_profile_count).length} instance(s)` : "Run an import to read live profiles"} />
        <KpiTile label="Drift from blueprint" value={drifting.length} unit={drifting.length === 1 ? "instance" : "instances"}
          note={drifting.length ? `Changed outside Fleet Control · ${drifting.map((i) => i.id).join(", ")}` : "Nothing changed outside Fleet Control"}
          tone={drifting.length ? "warning" : "success"} />
        <KpiTile label="Hermes versions" value={versions.length} unit="in use"
          note={versions.length ? versions.map(([v, n]) => `${v} (×${n})`).join(", ") : "Reported once an agent pairs"} />
      </div>

      <div className="grid grid-cols-[1fr_380px] gap-5 items-start">
        <Card className="overflow-hidden">
          <div className="flex gap-2 p-3 border-b border-hairline">
            <input className={`${INPUT} max-w-[280px]`} placeholder="Filter instances" value={query} onChange={(e) => setQuery(e.target.value)} />
            <select className={`${INPUT} max-w-[200px]`} value={env} onChange={(e) => setEnv(e.target.value as "all" | Environment)}>
              <option value="all">Environment: All</option>
              <option value="production">Production</option>
              <option value="staging">Staging</option>
              <option value="lab">Lab</option>
            </select>
          </div>
          <table className="w-full border-collapse">
            <thead>
              <tr className="bg-container-low text-left">
                {["Instance", "Environment", "Hermes", "Connection", "Applied", "Status"].map((h) => (
                  <th key={h} className="text-label uppercase text-text-secondary px-4 py-2.5 font-semibold">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.map((i) => {
                const s = instanceStatus(i, now);
                return (
                  <tr key={i.id} onClick={() => setSelected(i.id)}
                    className={`border-t border-hairline cursor-pointer ${current?.id === i.id ? "bg-container-low" : "hover:bg-surface"}`}>
                    <td className="px-4 py-3 font-semibold">{i.id}</td>
                    <td className="px-4 py-3">{ENV_LABEL[i.environment]}</td>
                    <td className="px-4 py-3"><Mono>{i.hermes_version ?? "—"}</Mono></td>
                    <td className="px-4 py-3 text-small">
                      {i.mode === "api-only" ? "API only" : i.agent_version ? <>Agent <Mono>{i.agent_version}</Mono> · {timeAgo(i.last_heartbeat, now)}</> : "Agent not paired"}
                    </td>
                    <td className="px-4 py-3 text-small">{i.applied ? `${i.applied.name} v${i.applied.version}` : <span className="text-text-secondary">Nothing applied</span>}</td>
                    <td className="px-4 py-3"><Chip tone={s.tone}>{s.label}</Chip></td>
                  </tr>
                );
              })}
              {shown.length === 0 && <tr><td colSpan={6} className="px-4 py-6 text-center text-text-secondary">No instance matches the filter.</td></tr>}
            </tbody>
          </table>
        </Card>
        {current && <InstanceRail key={current.id} inst={current} now={now} onChanged={() => void reload()} />}
      </div>
      {overlays}
    </>
  );
}

function InstanceRail({ inst, now, onChanged }: { inst: Instance; now: number; onChanged: () => void }) {
  const navigate = useNavigate();
  const { can } = useAuth();
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: Tone; text: string } | null>(null);
  const [importing, setImporting] = useState(false);
  const status = instanceStatus(inst, now);
  const rows = capabilityRows(inst);
  const agentReady = inst.mode === "agent" && Boolean(inst.agent_version);

  async function run(label: string, fn: () => Promise<unknown>, ok: string) {
    setBusy(label);
    setMsg(null);
    try {
      await fn();
      setMsg({ tone: "info", text: ok });
      onChanged();
    } catch (e) {
      setMsg({ tone: "error", text: errorText(e) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-section truncate">{inst.id}</div>
          <div className="text-small text-text-secondary mt-0.5">{ENV_LABEL[inst.environment]} · added {formatDate(inst.created_at)} by {inst.owner}</div>
        </div>
        <Chip tone={status.tone}>{status.label}</Chip>
      </div>

      <Label className="mt-5 mb-2">What Fleet Control can do here</Label>
      <div className="border border-hairline rounded-control divide-y divide-hairline">
        {rows.map((r) => (
          <div key={r.label} className="flex justify-between gap-3 px-3 py-2.5 text-[13px]">
            <span>{r.label}</span>
            <span className={`font-semibold text-right ${r.tone === "success" ? "text-success" : r.tone === "warning" ? "text-warning" : "text-text-secondary"}`}>{r.value}</span>
          </div>
        ))}
      </div>
      <p className="text-small text-text-secondary mt-2 mb-0">From the agent's capability report{inst.last_heartbeat ? `, last heartbeat ${timeAgo(inst.last_heartbeat, now)}` : ""}.</p>
      {inst.report?.notes?.map((n) => <Banner key={n} tone="warning" className="mt-3 text-small">{n}</Banner>)}

      <div className="grid grid-cols-2 gap-3 mt-5">
        <div className="rounded-control bg-container-low px-3 py-2.5">
          <div className="text-[20px] font-bold num">{inst.live_profile_count}</div>
          <div className="text-small text-text-secondary">Live profiles imported</div>
        </div>
        <div className="rounded-control bg-container-low px-3 py-2.5">
          <div className="text-[20px] font-bold num">{inst.applied ? `v${inst.applied.version}` : "—"}</div>
          <div className="text-small text-text-secondary truncate">{inst.applied ? `${inst.applied.name} applied` : "No blueprint applied"}</div>
        </div>
      </div>

      <div className="flex flex-col gap-2 mt-5">
        {inst.open_drift > 0 && (
          <Button variant="primary" icon="warning" onClick={() => navigate(`/instances/${inst.id}/drift`)}>
            Review drift ({inst.open_drift} field{inst.open_drift === 1 ? "" : "s"})
          </Button>
        )}
        {can("instances.operate") && <Button icon="download" disabled={!agentReady || busy !== null} title={agentReady ? undefined : "Needs a paired Fleet Control Agent"}
          onClick={() => void run("import", () => api.importProfiles(inst.id), "Import queued. The agent reads live profiles on its next poll.")}>
          {busy === "import" && <Spinner />}Import live profiles
        </Button>}
        {can("blueprints.write") && <Button icon="plus" disabled={!inst.live_profile_count || busy !== null}
          title={inst.live_profile_count ? undefined : "Import live profiles first"} onClick={() => setImporting(true)}>
          Create blueprint from what runs here
        </Button>}
        {can("instances.operate") && <Button icon="scan" disabled={!agentReady || !inst.applied || busy !== null}
          title={!inst.applied ? "Apply a blueprint version first; drift is measured against it" : undefined}
          onClick={() => inst.applied && void run("scan", () => api.driftScan(inst.id, inst.applied!.name, inst.applied!.version),
            `Drift scan against ${inst.applied.name} v${inst.applied.version} queued.`)}>
          {busy === "scan" && <Spinner />}Scan for drift
        </Button>}
        {can("plans.create") && <Button icon="layers" onClick={() => navigate(`/blueprints`)}>Plan a blueprint for this instance</Button>}
      </div>
      {msg && <Banner tone={msg.tone} className="mt-3">{msg.text}</Banner>}
      {importing && <ImportBlueprintModal instanceId={inst.id} onClose={() => { setImporting(false); onChanged(); }} />}
    </Card>
  );
}
