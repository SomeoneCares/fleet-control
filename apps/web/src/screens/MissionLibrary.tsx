import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { api, type Instance, type MissionDetail, type MissionMarketLevel, type MissionReach, type MissionSummary } from "../api/client";
import { useAuth } from "../lib/auth";
import { errorText, useLoad } from "../lib/hooks";
import { MARKET, NO_FILTERS, REACH, bySector, filterMissions, guardrails, workflowSteps, type MissionFilters } from "../lib/missions";
import { BLUEPRINT_NAME } from "../lib/names";
import { ENV_LABEL } from "../lib/view";
import { Banner, Button, Card, Chip, Field, INPUT, Icon, KpiTile, Label, Modal, Mono, PageHeader, Segmented, Spinner } from "../components/ui";

export function MissionLibraryScreen() {
  const [params, setParams] = useSearchParams();
  const { data, error, loading } = useLoad(api.missions, []);
  const [filters, setFilters] = useState<MissionFilters>(NO_FILTERS);
  const openId = params.get("mission");

  const header = (
    <PageHeader crumb="Design" title="Mission library"
      subtitle="Ready-made fleets for work on SAS. Each mission brings its agents, the SAS tools they may use, and the points where a person decides. Provisioning one creates a draft blueprint you plan and apply as usual." />
  );
  if (loading && !data) return <>{header}<div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading missions…</div></>;
  if (error && !data) return <>{header}<Banner tone="error">{error}</Banner></>;
  if (!data) return header;

  const shown = filterMissions(data.missions, filters);
  const groups = bySector(shown, data.sectors);
  const set = (patch: Partial<MissionFilters>) => setFilters((f) => ({ ...f, ...patch }));

  return (
    <>
      {header}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <KpiTile label="Missions" value={data.missions.length} note={`${Object.keys(data.sectors).length} sectors`} />
        <KpiTile label="Work on SAS today" value={data.missions.filter((m) => m.reach === "mcp-today").length} tone="success" note="Through the SAS Viya MCP Server" />
        <KpiTile label="Need a connector" value={data.missions.filter((m) => m.reach !== "mcp-today").length} tone="warning" note="SAS module APIs to wrap" />
        <KpiTile label="Open or thin market" value={data.missions.filter((m) => m.saturation === "open" || m.saturation === "thin").length} tone="success" note="No agent workforce sold yet" />
      </div>

      <Card className="flex flex-wrap items-center gap-3 px-4 py-3 mb-6">
        <input aria-label="Search missions" className={`${INPUT} w-64`} placeholder="Search missions or SAS modules" value={filters.text}
          onChange={(e) => set({ text: e.target.value })} />
        <select aria-label="Sector" className={`${INPUT} w-56`} value={filters.sector} onChange={(e) => set({ sector: e.target.value })}>
          <option value="">All sectors</option>
          {Object.entries(data.sectors).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <Segmented<MissionReach | "">
          label="SAS reach" value={filters.reach} onChange={(v) => set({ reach: v })}
          options={[{ value: "", label: "Any reach" }, ...(Object.keys(REACH) as MissionReach[]).map((r) => ({ value: r, label: REACH[r].label }))]} />
        <select aria-label="Market" className={`${INPUT} w-44`} value={filters.market} onChange={(e) => set({ market: e.target.value as MissionMarketLevel | "" })}>
          <option value="">Any market</option>
          {(Object.keys(MARKET) as MissionMarketLevel[]).map((k) => <option key={k} value={k}>{MARKET[k].label}</option>)}
        </select>
        <div className="flex-1" />
        <span className="text-small text-text-secondary">{shown.length} of {data.missions.length}</span>
        {(filters.text || filters.sector || filters.reach || filters.market) && <Button onClick={() => setFilters(NO_FILTERS)}>Clear</Button>}
      </Card>

      {groups.length === 0 && <Card className="p-6 text-text-secondary">No mission matches these filters.</Card>}
      {groups.map((g) => (
        <section key={g.sector} className="mb-7">
          <h2 className="text-section m-0 mb-3">{g.label}</h2>
          <div className="grid grid-cols-3 gap-4">
            {g.missions.map((m) => <MissionCard key={m.id} mission={m} onOpen={() => setParams({ mission: m.id })} />)}
          </div>
        </section>
      ))}

      {openId && <MissionModal id={openId} onClose={() => setParams({})} />}
    </>
  );
}

function MissionCard({ mission: m, onOpen }: { mission: MissionSummary; onOpen: () => void }) {
  return (
    <button type="button" onClick={onOpen} className="text-left cursor-pointer bg-white border border-hairline rounded-card p-5 flex flex-col gap-3 hover:border-primary focus:outline-none focus:ring-1 focus:ring-primary">
      <div className="font-semibold text-[14px] leading-5">{m.title}</div>
      <p className="m-0 text-small text-text-secondary line-clamp-3">{m.summary}</p>
      <div className="flex flex-wrap gap-1.5 mt-auto">
        <Chip tone={REACH[m.reach].tone}>{REACH[m.reach].label}</Chip>
        <Chip tone={MARKET[m.saturation].tone}>{MARKET[m.saturation].label}</Chip>
      </div>
      <div className="text-small text-text-secondary">
        {m.agents.length} agents · {m.gates} decision{m.gates === 1 ? "" : "s"} by people
      </div>
    </button>
  );
}

function MissionModal({ id, onClose }: { id: string; onClose: () => void }) {
  const { can } = useAuth();
  const { data: m, error } = useLoad(() => api.mission(id), [id]);
  const [provisioning, setProvisioning] = useState(false);
  const [showYaml, setShowYaml] = useState(false);

  if (provisioning && m) return <ProvisionModal mission={m} onClose={() => setProvisioning(false)} />;
  return (
    <Modal width={960} onClose={onClose}
      title={m?.title ?? id} subtitle={m ? `${m.sector_label} · ${m.summary}` : undefined}
      footer={<>
        <Button onClick={onClose}>Close</Button>
        {can("blueprints.write") && <Button variant="primary" icon="arrowRight" disabled={!m} onClick={() => setProvisioning(true)}>Provision…</Button>}
      </>}>
      {error ? <Banner tone="error">{error}</Banner> : !m ? <Spinner /> : <MissionBody mission={m} showYaml={showYaml} onToggleYaml={() => setShowYaml((s) => !s)} />}
    </Modal>
  );
}

function MissionBody({ mission: m, showYaml, onToggleYaml }: { mission: MissionDetail; showYaml: boolean; onToggleYaml: () => void }) {
  const g = guardrails(m.blueprint);
  const roles = Object.fromEntries(m.blueprint.agents.map((a) => [a.id, a.role]));
  return (
    <div className="flex flex-col gap-6">
      <p className="m-0 text-body"><span className="font-semibold">Why: </span>{m.value}</p>

      <div className="grid grid-cols-[3fr_2fr] gap-6">
        <div className="flex flex-col gap-6 min-w-0">
          <div>
            <Label className="mb-2">How it runs</Label>
            {m.blueprint.workflows.map((wf) => (
              <ol key={wf.id} className="m-0 p-0 list-none flex flex-col gap-2">
                {workflowSteps(wf.steps).map((s, i) => (
                  <li key={i} className="flex gap-3 items-start">
                    <span className="size-6 shrink-0 rounded-full bg-container-high text-[11px] font-bold flex items-center justify-center">{i + 1}</span>
                    {s.kind === "agents" ? (
                      <div className="text-body">
                        {s.agents.map((a) => <Mono key={a} className="mr-2">{a}</Mono>)}
                        {s.agents.length > 1 && <span className="text-small text-text-secondary">in parallel</span>}
                        {s.artifacts.length > 0 && <div className="text-small text-text-secondary">produces {s.artifacts.join(", ")}</div>}
                      </div>
                    ) : s.kind === "gate" ? (
                      <div className="text-body">
                        <span className="font-semibold">A person approves</span> <Chip tone="info">{s.role}</Chip>
                        <div className="text-small text-text-secondary">
                          within {s.timeout}{s.escalateTo ? `, then escalates to ${s.escalateTo}` : ""}{s.onReject ? `; sending back returns the work to ${s.onReject}` : ""}
                        </div>
                      </div>
                    ) : (
                      <div className="text-body">
                        <span className="font-semibold">Decision Room</span>
                        {s.question && <div className="text-small text-text-secondary">“{s.question}”</div>}
                      </div>
                    )}
                  </li>
                ))}
              </ol>
            ))}
          </div>

          <div>
            <Label className="mb-2">Agents</Label>
            <div className="flex flex-col gap-2">
              {m.blueprint.agents.map((a) => (
                <div key={a.id} className="border border-hairline rounded-control px-3 py-2">
                  <div className="flex items-center gap-2 flex-wrap"><Mono className="font-semibold">{a.id}</Mono>
                    {a.mcps.map((s) => <Chip key={s} tone={s === "sas-viya" ? "success" : "warning"}>{s}</Chip>)}
                  </div>
                  <div className="text-small text-text-secondary mt-0.5">{roles[a.id]}</div>
                  {a.soul.boundaries.length > 0 && (
                    <ul className="m-0 mt-1 pl-4 text-small">{a.soul.boundaries.map((b) => <li key={b}>{b}</li>)}</ul>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="flex flex-col gap-6 min-w-0">
          <div>
            <Label className="mb-2">People decide</Label>
            <div className="flex flex-col gap-2">
              {m.human_control.map((h, i) => (
                <div key={i} className="bg-primary-tint rounded-control px-3 py-2">
                  <div className="font-semibold text-body">{h.who}</div>
                  <div className="text-small">{h.decides}</div>
                </div>
              ))}
            </div>
          </div>

          <div>
            <Label className="mb-2">Guardrails</Label>
            <ul className="m-0 pl-4 text-small flex flex-col gap-1">
              <li>{g.deniedSas} SAS tools that change or run things are blocked</li>
              {g.approval.length > 0 && <li>Needs a person’s approval each time: {g.approval.map((t) => <Mono key={t} className="mr-1">{t}</Mono>)}</li>}
              {g.otherDenied.length > 0 && <li>Also blocked: {g.otherDenied.map((t) => <Mono key={t} className="mr-1">{t}</Mono>)}</li>}
              <li>{m.blueprint.tests.length} test{m.blueprint.tests.length === 1 ? "" : "s"} gate production</li>
            </ul>
          </div>

          <div>
            <Label className="mb-2">SAS</Label>
            <div className="flex items-center gap-2 mb-1"><Chip tone={REACH[m.sas.reach].tone}>{REACH[m.sas.reach].label}</Chip></div>
            <div className="text-small text-text-secondary mb-2">{REACH[m.sas.reach].hint}</div>
            <div className="text-small">{m.sas.modules.join(" · ")}</div>
            {m.sas.connectors.length > 0 && <div className="text-small mt-1">Connectors to build: {m.sas.connectors.map((c) => <Mono key={c} className="mr-1">{c}</Mono>)}</div>}
            {m.sas.mcp_tools.length > 0 && (
              <details className="mt-2 text-small">
                <summary className="cursor-pointer text-primary">{m.sas.mcp_tools.length} SAS MCP tools used</summary>
                <div className="mt-1 flex flex-wrap gap-1">{m.sas.mcp_tools.map((t) => <Mono key={t} className="bg-container-low rounded px-1.5">{t}</Mono>)}</div>
              </details>
            )}
          </div>

          <div>
            <Label className="mb-2">Market</Label>
            <div className="flex items-center gap-2 mb-1"><Chip tone={MARKET[m.market.saturation].tone}>{MARKET[m.market.saturation].label}</Chip></div>
            <div className="text-small text-text-secondary mb-1">{MARKET[m.market.saturation].hint}</div>
            {m.market.vendors.length > 0 && <ul className="m-0 pl-4 text-small">{m.market.vendors.map((v) => <li key={v}>{v}</li>)}</ul>}
            {m.market.sas_own && <div className="text-small mt-1"><span className="font-semibold">SAS itself: </span>{m.market.sas_own}</div>}
            {m.market.sources.length > 0 && (
              <details className="mt-1 text-small">
                <summary className="cursor-pointer text-primary">Sources</summary>
                <ul className="m-0 pl-4">{m.market.sources.map((s) => <li key={s} className="truncate"><a href={s} target="_blank" rel="noreferrer" className="text-primary">{s}</a></li>)}</ul>
              </details>
            )}
          </div>

          {m.kpis.length > 0 && (
            <div>
              <Label className="mb-2">Measures</Label>
              <ul className="m-0 pl-4 text-small">{m.kpis.map((k) => <li key={k}>{k}</li>)}</ul>
            </div>
          )}
        </div>
      </div>

      <div>
        <Button onClick={onToggleYaml}>{showYaml ? "Hide" : "Show"} the blueprint it creates</Button>
        {showYaml && <pre className="mt-3 mb-0 max-h-[50vh] overflow-auto p-4 rounded-control bg-container-low border border-hairline font-mono text-[12px] leading-[18px]">{m.blueprint_yaml}</pre>}
      </div>
    </div>
  );
}

function ProvisionModal({ mission, onClose }: { mission: MissionDetail; onClose: () => void }) {
  const navigate = useNavigate();
  const { data: instances } = useLoad(api.instances, []);
  const [target, setTarget] = useState("");
  const [name, setName] = useState(mission.id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ name: string; version: number; connectors: string[] } | null>(null);
  const ready = (instances ?? []).filter((i) => i.live_profile_count > 0);
  const choice = target || ready[0]?.id || "";
  const nameOk = BLUEPRINT_NAME.test(name);

  async function provision() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.provisionMission(mission.id, { instance_id: choice, name });
      setDone({ name: r.name, version: r.version, connectors: r.connectors_needed });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`Provision ${mission.title}`} width={620} onClose={onClose}
      subtitle="Creates a draft blueprint. Nothing changes on any instance until you plan, approve and apply it."
      footer={done ? <>
        <Button onClick={onClose}>Close</Button>
        <Button variant="primary" icon="arrowRight" onClick={() => navigate(`/blueprints?name=${encodeURIComponent(done.name)}`)}>Open in Blueprints</Button>
      </> : <>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={!choice || !nameOk || busy} onClick={() => void provision()}>{busy && <Spinner />}Create draft</Button>
      </>}>
      {done ? (
        <div className="flex flex-col gap-3">
          <Banner tone="success">Draft <Mono>{done.name}</Mono> v{done.version} created. Next: review it, then Plan… from Blueprints.</Banner>
          {done.connectors.length > 0 && (
            <Banner tone="warning">
              This mission also needs {done.connectors.map((c) => <Mono key={c} className="mr-1">{c}</Mono>)}
              on the instance. Until those MCP servers exist there, the plan will warn and those agents cannot reach their SAS module.
            </Banner>
          )}
        </div>
      ) : !instances ? <Spinner /> : (
        <>
          <Field label="Instance" hint="The agents run on this instance's default model. Only instances with imported live profiles can be chosen.">
            <select className={INPUT} value={choice} onChange={(e) => setTarget(e.target.value)} disabled={ready.length === 0}>
              {instances.map((i: Instance) => (
                <option key={i.id} value={i.id} disabled={i.live_profile_count === 0}>
                  {i.id} · {ENV_LABEL[i.environment]}{i.live_profile_count === 0 ? " · import live profiles first" : ""}
                </option>
              ))}
            </select>
          </Field>
          {ready.length === 0 && <Banner tone="info" className="mb-4">No instance has imported live profiles yet (Instances → Import live profiles).</Banner>}
          <Field label="Blueprint name" hint={nameOk ? "Provisioning the same name again adds a new version." : "Lowercase letters, digits and dashes; starts with a letter."}>
            <input className={INPUT} value={name} onChange={(e) => setName(e.target.value.trim())} />
          </Field>
          <div className="text-small text-text-secondary flex items-start gap-2">
            <Icon name="shield" className="mt-0.5" />
            <span>{mission.human_control.length} decision{mission.human_control.length === 1 ? "" : "s"} stay with people: {mission.human_control.map((h) => h.who).join(", ")}.</span>
          </div>
          {error && <Banner tone="error" className="mt-3">{error}</Banner>}
        </>
      )}
    </Modal>
  );
}
