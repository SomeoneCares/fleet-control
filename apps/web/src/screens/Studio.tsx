import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import { api, type AgentDoc, type AgentPatch, type BlueprintDetail, type OutputContract } from "../api/client";
import { useAuth, useMe } from "../lib/auth";
import { errorText } from "../lib/hooks";
import { cleanLines, renderSoul } from "../lib/soul";
import { formatValue, prettyId } from "../lib/view";
import { Banner, Button, Card, Chip, Field, INPUT, Icon, Label, Mono, PageHeader, Segmented, Spinner, TEXTAREA, TagInput, type IconName } from "../components/ui";
import { PlanModal } from "./Blueprints";
import { useBlueprintParams } from "./Designer";

type Tab = "identity" | "soul" | "model" | "skills" | "delegation" | "tests";
const TABS: { key: Tab; label: string; icon: IconName }[] = [
  { key: "identity", label: "Identity", icon: "bot" },
  { key: "soul", label: "SOUL", icon: "file" },
  { key: "model", label: "Model", icon: "spark" },
  { key: "skills", label: "Skills & tools", icon: "plug" },
  { key: "delegation", label: "Delegation & content", icon: "graph" },
  { key: "tests", label: "Tests", icon: "flask" },
];
const EDITABLE: (keyof AgentPatch)[] = ["role", "model", "soul", "skills", "toolsets", "mcps", "delegates_to", "content_zones", "tests"];

export function StudioScreen() {
  const { params, setParams, list, listError, reloadList, name, summary, version, data: detail, error, reload } = useBlueprintParams();
  const [notice, setNotice] = useState<string | null>(null);
  const agentId = params.get("agent") ?? detail?.parsed.agents[0]?.id ?? null;
  const agent = detail?.parsed.agents.find((a) => a.id === agentId) ?? null;

  if (listError || error) return <Banner tone="error">{listError ?? error}</Banner>;
  if (list && list.length === 0) {
    return (<>
      <PageHeader crumb="Design" title="Agent Studio" />
      <Card className="p-8 max-w-[640px]">No blueprints yet. <Link to="/blueprints">Import one in Blueprints</Link> to edit its agents.</Card>
    </>);
  }
  if (!detail || !agent || !name || !version) return <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div>;

  return (
    <AgentEditor key={`${name}/${version}/${agent.id}`} detail={detail} agent={agent} notice={notice}
      nextVersion={Math.max(version, ...(summary?.versions ?? [])) + 1}
      onSwitch={(id) => { setNotice(null); setParams({ name, version: String(version), agent: id }); }}
      onSaved={(v, text) => {
        setNotice(text);
        void reloadList(); // a new draft version changes the version list and the next version number
        if (v === version) void reload();
        else setParams({ name, version: String(v), agent: agent.id });
      }} />
  );
}

function AgentEditor({ detail, agent, notice, nextVersion, onSwitch, onSaved }: {
  detail: BlueprintDetail; agent: AgentDoc; notice: string | null; nextVersion: number;
  onSwitch: (id: string) => void; onSaved: (version: number, notice: string) => void;
}) {
  const [form, setForm] = useState<AgentDoc>(() => structuredClone(agent));
  const [tab, setTab] = useState<Tab>("soul");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [planning, setPlanning] = useState(false);
  const { can } = useAuth();
  const me = useMe();
  const canWrite = can("blueprints.write");
  const isDraft = detail.status === "draft";

  function cleaned(a: AgentDoc): AgentDoc {
    return { ...a, soul: { ...a.soul, principles: cleanLines(a.soul.principles), boundaries: cleanLines(a.soul.boundaries) } };
  }
  const patch: AgentPatch = {};
  const clean = cleaned(form);
  for (const k of EDITABLE) if (JSON.stringify(clean[k]) !== JSON.stringify(agent[k])) (patch as Record<string, unknown>)[k] = clean[k];
  const dirty = Object.keys(patch).length > 0;

  const set = <K extends keyof AgentDoc>(k: K, v: AgentDoc[K]) => setForm((f) => ({ ...f, [k]: v }));
  const setSoul = (s: Partial<AgentDoc["soul"]>) => setForm((f) => ({ ...f, soul: { ...f.soul, ...s } }));

  async function save() {
    setBusy(true);
    setFailure(null);
    try {
      let version = detail.version;
      if (!isDraft) version = (await api.createDraft(detail.name, detail.version)).version;
      const res = await api.editAgent(detail.name, version, agent.id, patch);
      onSaved(version, `Saved ${res.changed.length ? res.changed.join(", ") : "no changes"} to ${detail.name} v${version} (draft).`);
    } catch (e) {
      setFailure(errorText(e));
      setBusy(false);
    }
  }

  const saved = detail.managed[agent.hermes_profile ?? agent.id];
  const tests = detail.parsed.tests.filter((t) => form.tests.includes(t.id) || t.target === agent.id);

  return (
    <>
      <PageHeader
        crumb={<><Link to={`/designer?name=${detail.name}&version=${detail.version}`}>Design › {prettyId(detail.name)}</Link> › Agents</>}
        title={<span className="flex items-center gap-2.5 flex-wrap">{prettyId(agent.id)}
          <Chip tone={isDraft ? "warning" : "success"}>v{detail.version} {detail.status}</Chip>
          <Chip tone="info">{form.model.name}</Chip></span>}
        subtitle="Changes are saved to the blueprint draft; they reach Hermes only through a plan."
        actions={<>
          <select className={`${INPUT} w-auto`} aria-label="Agent" value={agent.id} disabled={dirty} title={dirty ? "Save or discard changes first" : undefined}
            onChange={(e) => onSwitch(e.target.value)}>
            {detail.parsed.agents.map((a) => <option key={a.id} value={a.id}>{prettyId(a.id)}</option>)}
          </select>
          {canWrite && dirty && <Button onClick={() => { setForm(structuredClone(agent)); setFailure(null); }}>Discard</Button>}
          {canWrite && (
            <Button variant="primary" icon="check" disabled={!dirty || busy} onClick={() => void save()}>
              {busy && <Spinner />}{isDraft ? "Save to draft" : `Save as draft v${nextVersion}`}
            </Button>
          )}
        </>} />
      {!canWrite && <Banner tone="info" className="mb-4">Read-only: the {me.role_label} role can read agents but not change them.</Banner>}
      {canWrite && !isDraft && <Banner tone="info" className="mb-4">v{detail.version} is {detail.status} and immutable. Saving creates draft v{nextVersion} with your changes.</Banner>}
      {notice && <Banner tone="success" className="mb-4">{notice}</Banner>}
      {failure && <Banner tone="error" className="mb-4">{failure}</Banner>}

      <div className="grid grid-cols-[210px_minmax(0,1fr)_320px] gap-5 items-start">
        <Card className="p-2">
          {TABS.map((t) => (
            <button key={t.key} type="button" onClick={() => setTab(t.key)}
              className={`w-full h-nav flex items-center gap-2.5 px-2.5 rounded-control text-[13px] cursor-pointer ${tab === t.key ? "bg-primary-tint text-primary font-semibold" : "font-medium hover:bg-container-low"}`}>
              <Icon name={t.icon} /><span className="flex-1 text-left">{t.label}</span>
              {t.key === "tests" && <span className="h-5 px-1.5 rounded-chip bg-container-low text-[11px] font-semibold flex items-center">{tests.length}</span>}
              {EDITABLE.some((k) => k in patch && tabOf(k) === t.key) && <span className="size-1.5 rounded-full bg-warning" aria-label="changed" />}
            </button>
          ))}
        </Card>

        <Card className="p-5">
          <fieldset disabled={!canWrite} className="m-0 p-0 border-0 min-w-0">
          {tab === "identity" && (
            <Panel title="Identity" hint="The id and Hermes profile name are fixed once the agent exists.">
              <div className="grid grid-cols-2 gap-4">
                <Field label="Agent id"><div className={`${INPUT} flex items-center bg-surface`}><Mono>{agent.id}</Mono></div></Field>
                <Field label="Hermes profile"><div className={`${INPUT} flex items-center bg-surface`}><Mono>{agent.hermes_profile ?? agent.id}</Mono></div></Field>
              </div>
              <Field label="Role" hint="One line; shown on the topology and written to the profile description.">
                <textarea className={TEXTAREA} rows={3} value={form.role} onChange={(e) => set("role", e.target.value)} />
              </Field>
            </Panel>
          )}

          {tab === "soul" && (
            <Panel title="SOUL" hint="Compiled into the profile's SOUL.md on apply; its hash is what drift detection watches."
              right={<Segmented label="SOUL mode" value={form.soul.raw != null ? "raw" : "structured"}
                options={[{ value: "structured", label: "Structured" }, { value: "raw", label: "Raw" }]}
                onChange={(m) => setSoul({ raw: m === "raw" ? renderSoul({ ...form.soul, raw: null }) : null })} />}>
              {form.soul.raw != null ? (
                <Field label="SOUL.md (raw)" hint="Raw text replaces the structured sections entirely.">
                  <textarea className={`${TEXTAREA} font-mono text-[12px] leading-[18px]`} rows={18} value={form.soul.raw} onChange={(e) => setSoul({ raw: e.target.value })} />
                </Field>
              ) : (<>
                <Field label="Objective"><textarea className={TEXTAREA} rows={3} value={form.soul.objective} onChange={(e) => setSoul({ objective: e.target.value })} /></Field>
                <Field label="Operating principles" hint="One per line.">
                  <textarea className={TEXTAREA} rows={4} value={form.soul.principles.join("\n")} onChange={(e) => setSoul({ principles: e.target.value.split("\n") })} />
                </Field>
                <Field label="Boundaries" hint="One per line. Hard limits the agent must respect.">
                  <textarea className={TEXTAREA} rows={3} value={form.soul.boundaries.join("\n")} onChange={(e) => setSoul({ boundaries: e.target.value.split("\n") })} />
                </Field>
                <OutputContractEditor value={form.soul.output_contract ?? null} onChange={(oc) => setSoul({ output_contract: oc })} />
              </>)}
            </Panel>
          )}

          {tab === "model" && (
            <Panel title="Model" hint="As Hermes knows them: the provider id and model id configured on the instance.">
              <div className="grid grid-cols-2 gap-4">
                <Field label="Provider"><input className={INPUT} value={form.model.provider} onChange={(e) => set("model", { ...form.model, provider: e.target.value })} /></Field>
                <Field label="Model"><input className={INPUT} value={form.model.name} onChange={(e) => set("model", { ...form.model, name: e.target.value })} /></Field>
                <Field label="Fallback model" hint="Optional; only where Hermes supports it for this provider.">
                  <input className={INPUT} value={form.model.fallback ?? ""} onChange={(e) => set("model", { ...form.model, fallback: e.target.value || null })} />
                </Field>
                <Field label="Data class" hint="Redacted-only marks a cloud model that may receive redacted summaries only.">
                  <select className={INPUT} value={form.model.data_class} onChange={(e) => set("model", { ...form.model, data_class: e.target.value as AgentDoc["model"]["data_class"] })}>
                    <option value="raw">Raw (on-premises or approved)</option>
                    <option value="redacted-only">Redacted summaries only</option>
                  </select>
                </Field>
              </div>
            </Panel>
          )}

          {tab === "skills" && (
            <Panel title="Skills & tools" hint="Names as Hermes knows them. Enter or comma adds one.">
              <Field label="Skills"><TagInput label="Add skill" values={form.skills} onChange={(v) => set("skills", v)} placeholder="e.g. name-matching" /></Field>
              <Field label="Toolsets"><TagInput label="Add toolset" values={form.toolsets} onChange={(v) => set("toolsets", v)} placeholder="e.g. web" /></Field>
              <Field label="MCP servers" hint="Registering an MCP server needs its URL or command, so it stays a manual step on the instance in Slice 1; drift reports the difference.">
                <TagInput label="Add MCP server" values={form.mcps} onChange={(v) => set("mcps", v)} placeholder="e.g. opensanctions" />
              </Field>
            </Panel>
          )}

          {tab === "delegation" && (
            <Panel title="Delegation & content" hint="Who this agent may hand work to, and which content zones it may read.">
              <Label className="mb-2">May delegate to</Label>
              <div className="flex flex-col gap-1.5 mb-5">
                {detail.parsed.agents.filter((a) => a.id !== agent.id).map((a) => (
                  <label key={a.id} className="flex items-center gap-2.5 text-[13px] cursor-pointer">
                    <input type="checkbox" className="accent-primary" checked={form.delegates_to.includes(a.id)}
                      onChange={(e) => set("delegates_to", e.target.checked ? [...form.delegates_to, a.id] : form.delegates_to.filter((x) => x !== a.id))} />
                    {prettyId(a.id)} <Mono className="text-text-secondary">{a.id}</Mono>
                  </label>
                ))}
              </div>
              <Field label="Content zones" hint="Enforced with the person's own access (effective access = person ∩ agent ∩ system).">
                <TagInput label="Add content zone" values={form.content_zones} onChange={(v) => set("content_zones", v)} placeholder="e.g. case-files" />
              </Field>
            </Panel>
          )}

          {tab === "tests" && (
            <Panel title={`Tests · ${tests.length}`} hint="Defined in the blueprint. Running them against a lab instance arrives with Test Lab (Slice 3).">
              {tests.length === 0 && <p className="m-0 text-text-secondary">No tests target this agent.</p>}
              <div className="flex flex-col gap-3">
                {tests.map((t) => (
                  <div key={t.id} className="border border-hairline rounded-control p-3">
                    <div className="flex items-center justify-between gap-2"><span className="font-semibold">{prettyId(t.id)}</span><Chip tone="neutral">Not run</Chip></div>
                    <p className="text-[13px] mt-1 mb-2">{t.scenario}</p>
                    <div className="text-small text-text-secondary flex flex-wrap gap-x-4 gap-y-1">
                      {t.required_tools.length > 0 && <span>Must call <Mono>{t.required_tools.join(", ")}</Mono></span>}
                      {t.forbidden_tools.length > 0 && <span>Must not call <Mono>{t.forbidden_tools.join(", ")}</Mono></span>}
                      <span>Evaluator <Mono>{t.evaluator}</Mono></span>
                      {t.expected_artifact && <span>Artifact <Mono>{t.expected_artifact}</Mono></span>}
                    </div>
                  </div>
                ))}
              </div>
            </Panel>
          )}
          </fieldset>
        </Card>

        <div className="flex flex-col gap-4">
          <Card className="p-4">
            <h2 className="text-section m-0 mb-1">On Hermes</h2>
            <p className="text-small text-text-secondary mt-0 mb-3">What the agent reconciles on profile <Mono>{agent.hermes_profile ?? agent.id}</Mono> when v{detail.version} is applied (as saved).</p>
            {saved && (
              <div className="border border-hairline rounded-control divide-y divide-hairline text-[13px]">
                {([["Description", saved.description], ["Model", formatValue("model", saved.model)], ["SOUL", formatValue("soul_sha256", saved.soul_sha256)],
                  ["Skills", `${saved.skills.length}`], ["Toolsets", `${saved.toolsets.length}`], ["MCP servers", `${saved.mcps.length} (manual)`]] as [string, string][]).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3 px-3 py-2"><span className="text-text-secondary shrink-0">{k}</span><span className="text-right truncate" title={v}>{v}</span></div>
                ))}
              </div>
            )}
            {can("plans.create") && <Button className="w-full mt-3" icon="arrowRight" disabled={dirty} title={dirty ? "Save first" : undefined} onClick={() => setPlanning(true)}>Plan v{detail.version}…</Button>}
          </Card>
          <Card className="p-4">
            <h2 className="text-section m-0 mb-1">Sandbox</h2>
            <p className="text-small text-text-secondary m-0">Trying the draft SOUL on a lab instance uses the agent's <Mono>run_test</Mono> job and arrives with Test Lab (Slice 3). Nothing here writes to Hermes.</p>
          </Card>
        </div>
      </div>
      {planning && <PlanModal name={detail.name} version={detail.version} onClose={() => setPlanning(false)} />}
    </>
  );
}

function tabOf(k: keyof AgentPatch): Tab {
  if (k === "role") return "identity";
  if (k === "soul") return "soul";
  if (k === "model") return "model";
  if (k === "skills" || k === "toolsets" || k === "mcps") return "skills";
  if (k === "tests") return "tests";
  return "delegation";
}

function Panel({ title, hint, right, children }: { title: string; hint: string; right?: ReactNode; children: ReactNode }) {
  return (
    <>
      <div className="flex items-start justify-between gap-3 mb-4">
        <div><h2 className="text-section m-0">{title}</h2><p className="text-small text-text-secondary m-0 mt-0.5">{hint}</p></div>
        {right}
      </div>
      {children}
    </>
  );
}

function OutputContractEditor({ value, onChange }: { value: OutputContract | null; onChange: (v: OutputContract | null) => void }) {
  return (
    <div>
      <Label className="mb-1.5">Output contract</Label>
      <div className="grid grid-cols-[160px_minmax(0,1fr)] gap-3 items-start">
        <select className={INPUT} aria-label="Output format" value={value?.format ?? "none"}
          onChange={(e) => onChange(e.target.value === "none" ? null : { format: e.target.value as OutputContract["format"], required: value?.required ?? [], artifact: value?.artifact ?? null })}>
          <option value="none">No contract</option>
          <option value="json">JSON</option>
          <option value="markdown">Markdown</option>
          <option value="text">Text</option>
          <option value="file">File</option>
        </select>
        {value && (value.format === "file" ? (
          <input className={INPUT} aria-label="Expected artifact" placeholder="sar-draft.docx" value={value.artifact ?? ""} onChange={(e) => onChange({ ...value, artifact: e.target.value || null })} />
        ) : (
          <TagInput label="Add required key" values={value.required} onChange={(r) => onChange({ ...value, required: r })} placeholder={value.format === "json" ? "required keys, e.g. match_count" : "required sections"} />
        ))}
      </div>
    </div>
  );
}
