import { useState } from "react";
import { api, type AccessInspection, type AccessLine } from "../api/client";
import { errorText, useLoad } from "../lib/hooks";
import { groupByArea } from "../lib/access";
import { Banner, Button, Card, Chip, Icon, INPUT, Mono, Spinner } from "../components/ui";

// Build document §7: effective access = person ∩ agent ∩ system, each line with the rule that decides it.
export function AccessInspector() {
  const { data: subjects, error: loadError } = useLoad(api.accessSubjects, []);
  const [email, setEmail] = useState("");
  const [agentKey, setAgentKey] = useState("");
  const [tool, setTool] = useState("");
  const [room, setRoom] = useState("");
  const [environment, setEnvironment] = useState("");
  const [result, setResult] = useState<AccessInspection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [blueprint, agent] = agentKey ? agentKey.split("/") : ["", ""];

  async function inspect() {
    setBusy(true); setError(null);
    try {
      setResult(await api.accessInspect({ email: email || undefined, blueprint: blueprint || undefined, agent: agent || undefined,
                                          tool: tool.trim() || undefined, room: room || undefined, environment: environment || undefined }));
    } catch (e) { setError(errorText(e)); setResult(null); } finally { setBusy(false); }
  }

  return (
    <Card className="p-5 mb-5">
      <h2 className="text-section m-0">Check effective access</h2>
      <p className="text-small text-text-secondary mt-1 mb-4">
        What a person may do, what an agent may reach, and what reaches the person through the agent. A connected system's own
        permissions stay with that system; the inspector says where it decides instead of guessing.
      </p>
      {loadError && <Banner tone="error">{loadError}</Banner>}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3 items-end">
        <Pick label="Person">
          <select className={INPUT} value={email} onChange={(e) => setEmail(e.target.value)}>
            <option value="">—</option>
            {subjects?.people.map((p) => <option key={p.email} value={p.email}>{p.name || p.email} ({p.role_label}){p.disabled ? " · disabled" : ""}</option>)}
          </select>
        </Pick>
        <Pick label="Agent">
          <select className={INPUT} value={agentKey} onChange={(e) => setAgentKey(e.target.value)}>
            <option value="">—</option>
            {subjects?.agents.map((a) => <option key={`${a.blueprint}/${a.agent}`} value={`${a.blueprint}/${a.agent}`}>{a.agent} ({a.blueprint} v{a.version})</option>)}
          </select>
        </Pick>
        <Pick label="Tool (needs an agent)">
          <input className={INPUT} list="access-tools" value={tool} onChange={(e) => setTool(e.target.value)} placeholder="sas-viya.list_caslibs" />
          <datalist id="access-tools">{subjects?.tools.map((t) => <option key={t} value={t} />)}</datalist>
        </Pick>
        <Pick label="Decide in room (needs a person)">
          <select className={INPUT} value={room} onChange={(e) => setRoom(e.target.value)}>
            <option value="">—</option>
            {subjects?.rooms.map((r) => <option key={r.id} value={r.id}>{r.question.slice(0, 60)}{r.case ? ` · ${r.case}` : ""}</option>)}
          </select>
        </Pick>
        <Pick label="Apply a plan to (needs a person)">
          <select className={INPUT} value={environment} onChange={(e) => setEnvironment(e.target.value)}>
            <option value="">—</option><option value="lab">lab</option><option value="staging">staging</option><option value="production">production</option>
          </select>
        </Pick>
        <Button variant="primary" disabled={busy || (!email && !agentKey)} onClick={() => void inspect()}>{busy && <Spinner />}Inspect</Button>
      </div>
      {error && <Banner tone="error" className="mt-4">{error}</Banner>}
      {result && <Result doc={result} />}
    </Card>
  );
}

function Pick({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="flex flex-col gap-1.5"><span className="text-label uppercase text-text-secondary">{label}</span>{children}</label>;
}

function Verdict({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 items-start text-[13px] py-1">
      <Icon name={ok ? "checkCircle" : "xCircle"} size={15} className={`${ok ? "text-success" : "text-error"} shrink-0 mt-0.5`} />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function Lines({ lines, show }: { lines: AccessLine[]; show: "allowed" | "denied" }) {
  const shown = lines.filter((l) => (show === "allowed" ? l.allowed : !l.allowed));
  if (!shown.length) return <p className="m-0 text-small text-text-secondary">None.</p>;
  return <>{shown.map((l) => <Verdict key={l.key} ok={l.allowed}>{l.label} <span className="text-text-secondary">· {l.why}</span></Verdict>)}</>;
}

function Result({ doc }: { doc: AccessInspection }) {
  const [denied, setDenied] = useState(false);
  return (
    <div className="mt-5 flex flex-col gap-4">
      {doc.checks.length > 0 && (
        <div className="border border-hairline rounded-control p-3.5 flex flex-col gap-1">
          {doc.checks.map((c) => (
            <Verdict key={c.question} ok={c.allowed}>
              <span className="font-medium">{c.question}</span> <b>{c.allowed ? "Yes" : "No"}</b>{c.needs_approval ? ", with a person's approval each time" : ""}:
              {" "}<span className="text-text-secondary">{c.why}</span>
              {c.system && <div className="text-small text-text-secondary">System: {c.system}{c.system_health ? ` (now ${c.system_health})` : ""}</div>}
            </Verdict>
          ))}
        </div>
      )}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-4 items-start">
        {doc.person && (
          <div className="border border-hairline rounded-control p-3.5">
            <div className="flex items-center justify-between gap-2 mb-2">
              <div className="font-semibold text-[13px]">{doc.person.name || doc.person.email}</div>
              <Chip tone="info">{doc.person.role_label}</Chip>
            </div>
            <div className="text-small text-text-secondary mb-2">
              {doc.person.summary.permissions} of {doc.person.summary.of_permissions} permissions · reads {doc.person.summary.zones} of {doc.person.summary.of_zones} zones
            </div>
            <label className="flex items-center gap-1.5 text-small mb-2 cursor-pointer">
              <input type="checkbox" checked={denied} onChange={(e) => setDenied(e.target.checked)} />Show what they may not do
            </label>
            {groupByArea(doc.person.permissions).map(([area, lines]) => (
              <div key={area} className="mb-2">
                <div className="text-label uppercase text-text-secondary">{area}</div>
                <Lines lines={lines.map((p) => ({ key: p.permission, label: p.label, allowed: p.allowed, why: p.why }))} show={denied ? "denied" : "allowed"} />
              </div>
            ))}
            <div className="text-label uppercase text-text-secondary mt-2">Content zones</div>
            {doc.person.zones.map((z) => <Verdict key={z.zone} ok={z.allowed}><Mono>{z.zone}</Mono> <span className="text-text-secondary">· {z.why}</span></Verdict>)}
          </div>
        )}
        {doc.agent && (
          <div className="border border-hairline rounded-control p-3.5">
            <div className="flex items-center justify-between gap-2 mb-2">
              <div className="font-semibold text-[13px]">{doc.agent.agent}</div>
              <Chip tone="neutral">{doc.agent.blueprint} v{doc.agent.version}</Chip>
            </div>
            <div className="text-small text-text-secondary mb-2">Runs as profile <Mono>{doc.agent.profile}</Mono>{doc.agent.model?.name ? ` on ${doc.agent.model.name}` : ""}</div>
            <Row label="MCP servers">{doc.agent.mcps.join(", ") || "none"}</Row>
            <Row label="Toolsets">{doc.agent.toolsets.join(", ") || "none"}</Row>
            <Row label="Blocked">{doc.agent.blocked.map((b) => `${b.tool} (${b.policy})`).join(", ") || "nothing"}</Row>
            <Row label="Needs approval">{doc.agent.approval.map((b) => `${b.tool} (${b.policy})`).join(", ") || "nothing"}</Row>
            <div className="text-label uppercase text-text-secondary mt-2">Content zones</div>
            {doc.agent.zones.map((z) => <Verdict key={z.zone} ok={z.allowed}><Mono>{z.zone}</Mono> <span className="text-text-secondary">· {z.why}</span></Verdict>)}
          </div>
        )}
        {doc.together && (
          <div className="border border-hairline rounded-control p-3.5">
            <div className="font-semibold text-[13px] mb-1">Together</div>
            <div className="text-small text-text-secondary mb-2">{doc.together.note}</div>
            {doc.together.zones.length === 0 && <p className="m-0 text-small text-text-secondary">No zone either of them reads.</p>}
            {doc.together.zones.map((z) => <Verdict key={z.zone} ok={z.allowed}><Mono>{z.zone}</Mono> <span className="text-text-secondary">· {z.why}</span></Verdict>)}
          </div>
        )}
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="grid grid-cols-[110px_minmax(0,1fr)] gap-2 text-[13px] py-0.5"><span className="text-text-secondary">{label}</span><span className="break-words">{children}</span></div>;
}
