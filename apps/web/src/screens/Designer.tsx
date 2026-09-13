import { useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { api, type AgentDoc, type BlueprintDetail, type BlueprintSummary } from "../api/client";
import { useAuth } from "../lib/auth";
import { useLoad } from "../lib/hooks";
import { NODE_H, NODE_W, layoutTopology, modelChip, type TNode } from "../lib/topology";
import { prettyId } from "../lib/view";
import { Banner, Button, Card, Chip, INPUT, Icon, Label, Mono, PageHeader, Spinner } from "../components/ui";
import { PlanModal } from "./Blueprints";

type Selection = { agent: string } | { node: TNode };

const HEAD: Record<TNode["kind"], string> = {
  agent: "bg-container-low text-text-secondary",
  gate: "bg-warning-tint text-warning",
  output: "bg-container-high text-primary",
};
const CHIP: Record<TNode["kind"], string> = {
  agent: "bg-secondary-tint text-secondary",
  gate: "bg-warning-tint text-warning",
  output: "bg-container-high text-primary",
};
const KIND_LABEL: Record<TNode["kind"], string> = { agent: "Agent", gate: "Human gate", output: "Output" };

export function useBlueprintParams() {
  const [params, setParams] = useSearchParams();
  const { data: list, error: listError, reload: reloadList } = useLoad(api.blueprints, []);
  const name = params.get("name") ?? list?.[0]?.name ?? null;
  const summary: BlueprintSummary | null = list?.find((b) => b.name === name) ?? null;
  const version = Number(params.get("version")) || summary?.latest || null;
  const detailState = useLoad<BlueprintDetail | null>(
    () => (name && version ? api.blueprint(name, version) : Promise.resolve(null)),
    [name, version],
  );
  // While a new version loads, useLoad still holds the previous one. Never hand that out: an editor
  // initialised from stale data would save the old values over the new version.
  const loaded = detailState.data;
  const data = loaded && loaded.name === name && loaded.version === version ? loaded : null;
  return { params, setParams, list, listError, reloadList, name, summary, version, ...detailState, data };
}

export function DesignerScreen() {
  const { setParams, list, listError, name, summary, version, data: detail, error } = useBlueprintParams();
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [sel, setSel] = useState<Selection | null>(null);
  const [planning, setPlanning] = useState(false);
  const { can } = useAuth();

  if (listError) return <Banner tone="error">{listError}</Banner>;
  if (!list) return <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div>;
  if (list.length === 0) {
    return (<>
      <PageHeader crumb="Design" title="Fleet Designer" />
      <Card className="p-8 max-w-[640px]">No blueprints yet. <Link to="/blueprints">Import one in Blueprints</Link> to see its topology.</Card>
    </>);
  }
  if (error) return <Banner tone="error">{error}</Banner>;
  if (!detail || !name || !version) return <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading the blueprint…</div>;

  const doc = detail.parsed;
  const wf = workflowId ?? doc.workflows[0]?.id ?? null;
  const topo = layoutTopology(doc, wf);
  const agentId = sel && "agent" in sel ? sel.agent : sel && "node" in sel ? sel.node.agent ?? null : null;
  const node = sel && "node" in sel ? sel.node : null;
  const applied = summary?.applied_on ?? [];

  return (
    <>
      <PageHeader crumb="Design"
        title={<span className="flex items-center gap-3">{prettyId(doc.metadata.name)}<Chip tone={detail.status === "applied" ? "success" : "neutral"}>Blueprint v{version} · {detail.status}</Chip></span>}
        subtitle={applied.length ? `Applied: ${applied.map((a) => `v${a.version} on ${a.instance}`).join(", ")}` : "Not applied to any instance yet"}
        actions={<>
          <select className={`${INPUT} w-auto`} aria-label="Version" value={version}
            onChange={(e) => { setSel(null); setParams({ name, version: e.target.value }); }}>
            {(summary?.versions ?? [version]).slice().reverse().map((v) => <option key={v} value={v}>v{v}</option>)}
          </select>
          {can("plans.create") && <Button variant="primary" icon="arrowRight" onClick={() => setPlanning(true)}>Create plan</Button>}
        </>} />

      <div className="grid grid-cols-[230px_minmax(0,1fr)_330px] gap-5 items-start">
        <Card className="overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3.5 border-b border-hairline">
            <h2 className="text-section m-0">Blueprint</h2><Mono className="text-text-secondary">v{version}</Mono>
          </div>
          <div className="p-2 text-[13px]">
            <OutlineHead icon="bot" label="Agents" count={doc.agents.length} />
            {doc.agents.map((a) => (
              <button key={a.id} type="button" onClick={() => setSel({ agent: a.id })}
                className={`w-full text-left h-[30px] pl-9 pr-2 rounded-control cursor-pointer truncate ${agentId === a.id ? "bg-secondary-tint text-secondary font-semibold" : "hover:bg-container-low"}`}>
                {prettyId(a.id)}
              </button>
            ))}
            <OutlineHead icon="flow" label="Workflows" count={doc.workflows.length} />
            {doc.workflows.map((w) => (
              <button key={w.id} type="button" onClick={() => setWorkflowId(w.id)}
                className={`w-full text-left h-[30px] pl-9 pr-2 rounded-control cursor-pointer truncate ${wf === w.id ? "bg-primary-tint text-primary font-semibold" : "hover:bg-container-low"}`}>
                {prettyId(w.id)}
              </button>
            ))}
            {doc.workflows.length === 0 && <div className="pl-9 text-small text-text-secondary h-[30px] flex items-center">None; showing delegation</div>}
            <OutlineHead icon="flask" label="Tests" count={doc.tests.length} />
            <OutlineHead icon="shield" label="Policies" count={doc.policies.length} />
            {doc.policies.map((p) => (
              <div key={p.id} className="pl-9 pr-2 py-1 text-small flex items-center justify-between gap-2">
                <span className="truncate" title={p.description}>{p.id}</span>
                <Chip tone={p.enforcement === "block" ? "error" : p.enforcement === "approve" ? "warning" : "neutral"} className="h-5">{p.enforcement}</Chip>
              </div>
            ))}
            <OutlineHead icon="message" label="Delivery rules" count={doc.delivery.length} />
          </div>
        </Card>

        <Card className="overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3.5 border-b border-hairline">
            <h2 className="text-section m-0">Topology</h2>
            <span className="text-small text-text-secondary">{topo.mode === "workflow" ? `Workflow: ${prettyId(wf ?? "")}` : "Delegation (no workflow in this blueprint)"}</span>
          </div>
          <div className="overflow-auto [background-image:radial-gradient(var(--color-hairline)_1px,transparent_1px)] [background-size:16px_16px]">
            <div className="relative mx-auto" style={{ width: topo.width, height: topo.height }}>
              <svg width={topo.width} height={topo.height} className="absolute inset-0 text-outline" fill="none" stroke="currentColor" strokeWidth={1.5} aria-hidden="true">
                <defs>
                  <marker id="fd-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse">
                    <path d="M1 1 7 4 1 7" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
                  </marker>
                </defs>
                {topo.edges.map((e) => <path key={`${e.from}>${e.to}`} d={e.d} markerEnd="url(#fd-arrow)" strokeLinejoin="round" />)}
              </svg>
              {topo.nodes.map((n) => (
                <NodeBox key={n.id} node={n} selected={Boolean(n.agent && n.agent === agentId) || node?.id === n.id}
                  onClick={() => setSel(n.agent ? { agent: n.agent } : { node: n })} />
              ))}
            </div>
          </div>
          {topo.unplaced.length > 0 && (
            <div className="px-4 py-2.5 border-t border-hairline text-small text-text-secondary">
              Not in this workflow: {topo.unplaced.map(prettyId).join(", ")}
            </div>
          )}
          <div className="flex items-center gap-4 px-4 py-2.5 border-t border-hairline text-small text-text-secondary">
            {(["agent", "gate", "output"] as const).map((k) => (
              <span key={k} className="inline-flex items-center gap-1.5"><span className={`size-3 rounded-sm border border-border ${HEAD[k]}`} />{KIND_LABEL[k]}</span>
            ))}
            <span className="flex-1" />
            <span>Read-mostly: edit agents in Agent Studio</span>
          </div>
        </Card>

        <Card className="p-4">
          {agentId ? <AgentInspector detail={detail} agent={doc.agents.find((a) => a.id === agentId)!} />
            : node ? <NodeInspector node={node} />
            : <p className="m-0 text-text-secondary">Select an agent, gate or output on the topology to inspect it.</p>}
        </Card>
      </div>
      {planning && <PlanModal name={name} version={version} onClose={() => setPlanning(false)} />}
    </>
  );
}

function OutlineHead({ icon, label, count }: { icon: "bot" | "flow" | "flask" | "shield" | "message"; label: string; count: number }) {
  return (
    <div className="h-8 flex items-center gap-2 px-2 mt-1 font-semibold">
      <Icon name={icon} className="text-secondary" /><span className="flex-1">{label}</span><span className="font-normal text-text-secondary">{count}</span>
    </div>
  );
}

function NodeBox({ node, selected, onClick }: { node: TNode; selected: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-label={`${KIND_LABEL[node.kind]}: ${node.label}`}
      style={{ left: node.x, top: node.y, width: NODE_W, height: NODE_H }}
      className={`absolute text-left bg-white rounded-control overflow-hidden flex flex-col cursor-pointer ${selected ? "border-2 border-secondary" : "border border-border hover:border-outline"}`}>
      <div className={`h-6 shrink-0 flex items-center gap-1.5 px-2.5 text-label uppercase ${selected && node.kind === "agent" ? "bg-secondary-tint text-secondary" : HEAD[node.kind]}`}>
        <Icon name={node.kind === "agent" ? "bot" : node.kind === "gate" ? "lock" : "file"} size={12} />{KIND_LABEL[node.kind]}
      </div>
      <div className="flex-1 min-h-0 px-2.5 py-2 flex flex-col gap-1">
        <div className="text-[13px] leading-[18px] font-semibold truncate">{node.label}</div>
        <div className="text-small text-text-secondary truncate">{node.sub}</div>
        {node.chip && <span className={`mt-auto self-start max-w-full truncate inline-flex items-center h-[22px] px-2 rounded-chip text-small font-semibold ${CHIP[node.kind]}`}>{node.chip}</span>}
      </div>
    </button>
  );
}

function Section({ label, children }: { label: string; children: ReactNode }) {
  return <div className="mb-4"><Label className="mb-1.5">{label}</Label>{children}</div>;
}

function Tags({ values, empty = "None" }: { values: string[]; empty?: string }) {
  if (!values.length) return <div className="text-[13px] text-outline">{empty}</div>;
  return <div className="flex flex-wrap gap-1.5">{values.map((v) => <span key={v} className="inline-flex items-center h-[22px] px-2 rounded-chip bg-container-low text-small font-medium">{v}</span>)}</div>;
}

function AgentInspector({ detail, agent }: { detail: BlueprintDetail; agent: AgentDoc }) {
  const doc = detail.parsed;
  const residency = doc.policies.find((p) => p.kind === "data-residency" && (p.applies_to.includes("*") || p.applies_to.includes(agent.id)));
  const tests = doc.tests.filter((t) => agent.tests.includes(t.id) || t.target === agent.id);
  const delegatedBy = doc.agents.filter((a) => a.delegates_to.includes(agent.id)).map((a) => a.id);
  return (
    <>
      <div className="flex items-start justify-between gap-3 mb-4">
        <div className="min-w-0">
          <h2 className="text-section m-0 truncate">Inspector · {prettyId(agent.id)}</h2>
          <Mono className="text-text-secondary">profile {agent.hermes_profile ?? agent.id}</Mono>
        </div>
        <Chip tone="info">Agent</Chip>
      </div>
      <Section label="Role"><p className="m-0 text-[13px]">{agent.role}</p></Section>
      <Section label="Model">
        <span className="inline-flex items-center h-[22px] px-2 rounded-chip bg-secondary-tint text-secondary text-small font-semibold">{modelChip(agent)} ({agent.model.provider})</span>
        {agent.model.data_class === "redacted-only" && (
          <Banner tone="warning" className="mt-2 text-small">
            Cloud model: may only receive redacted summaries{residency ? ` (policy: ${residency.id})` : ""}.
          </Banner>
        )}
      </Section>
      <Section label="Skills"><Tags values={agent.skills} /></Section>
      <Section label="Toolsets"><Tags values={agent.toolsets} /></Section>
      <Section label="MCP servers"><Tags values={agent.mcps} /></Section>
      <Section label="Boundaries">
        {agent.soul.boundaries.length ? (
          <div className="border border-hairline rounded-control divide-y divide-hairline">
            {agent.soul.boundaries.map((b) => <div key={b} className="flex gap-2 px-3 py-2 text-[13px]"><Icon name="lock" className="text-text-secondary mt-0.5" />{b}</div>)}
          </div>
        ) : <div className="text-[13px] text-outline">None in SOUL</div>}
      </Section>
      <Section label="Delegation">
        <div className="text-[13px]">
          <div>Delegates to: {agent.delegates_to.length ? agent.delegates_to.map(prettyId).join(", ") : "nobody"}</div>
          <div className="text-text-secondary">Receives work from: {delegatedBy.length ? delegatedBy.map(prettyId).join(", ") : "nobody"}</div>
        </div>
      </Section>
      <Section label={`Tests attached · ${tests.length}`}>
        {tests.length ? (
          <div className="border border-hairline rounded-control divide-y divide-hairline">
            {tests.map((t) => <div key={t.id} className="flex items-center justify-between gap-2 px-3 py-2 text-[13px]"><span className="truncate">{prettyId(t.id)}</span><Chip tone="neutral" className="h-5">Not run</Chip></div>)}
          </div>
        ) : <div className="text-[13px] text-outline">None</div>}
      </Section>
      <div className="pt-3 border-t border-hairline">
        <Link to={`/studio?name=${detail.name}&version=${detail.version}&agent=${agent.id}`} className="inline-flex items-center gap-1.5 text-[13px] font-medium no-underline">
          Open in Agent Studio <Icon name="arrowRight" size={14} />
        </Link>
      </div>
    </>
  );
}

function NodeInspector({ node }: { node: TNode }) {
  return (
    <>
      <div className="flex items-start justify-between gap-3 mb-4">
        <h2 className="text-section m-0">Inspector · {node.label}</h2>
        <Chip tone={node.kind === "gate" ? "warning" : "info"}>{KIND_LABEL[node.kind]}</Chip>
      </div>
      <Section label={node.kind === "gate" ? "Gate" : "What happens"}><p className="m-0 text-[13px]">{node.sub}</p></Section>
      <Section label="Workflow step"><Mono>#{(node.step ?? 0) + 1}</Mono></Section>
      <p className="m-0 text-small text-text-secondary">
        {node.kind === "gate"
          ? "Human gates are recorded as decisions in the Workspace, never as chat replies. Editing workflows arrives in Slice 5."
          : "Decision Rooms open in the Workspace for approvers (Slice 4)."}
      </p>
    </>
  );
}
