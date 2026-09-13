import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { api, type BlueprintSummary, type BlueprintVersionInfo, type Instance } from "../api/client";
import { errorText, useLoad } from "../lib/hooks";
import { ENV_LABEL, formatDate, timeAgo } from "../lib/view";
import { Banner, Button, Card, Chip, Field, INPUT, Icon, Label, Modal, Mono, PageHeader, Spinner } from "../components/ui";

export function BlueprintsScreen() {
  const [params, setParams] = useSearchParams();
  const { data: list, error, loading, reload } = useLoad(api.blueprints, []);
  const [importing, setImporting] = useState(false);
  const selectedName = params.get("name") ?? list?.[0]?.name ?? null;
  const selected = list?.find((b) => b.name === selectedName) ?? null;

  const header = (
    <PageHeader crumb="Library" title="Blueprints" subtitle="Versioned fleet definitions. Import from YAML; each version is immutable once applied."
      actions={<Button variant="primary" icon="upload" onClick={() => setImporting(true)}>Import YAML</Button>} />
  );
  const importModal = importing && (
    <ImportModal onClose={() => setImporting(false)} onImported={(name) => { setImporting(false); void reload(); setParams({ name }); }} />
  );

  if (loading && !list) return <>{header}<div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading blueprints…</div></>;
  if (error && !list) return <>{header}<Banner tone="error">{error}</Banner></>;
  if (!list?.length) {
    return (
      <>
        {header}
        <Card className="max-w-[640px] mx-auto mt-10 px-10 py-12 text-center">
          <div className="mx-auto mb-5 size-14 rounded-card bg-primary-tint text-primary flex items-center justify-center"><Icon name="layers" size={26} /></div>
          <h2 className="text-section m-0">No blueprints yet</h2>
          <p className="text-text-secondary mt-2 mb-6">Import a Fleet Blueprint (YAML, <Mono>apiVersion: fleetcontrol/v1</Mono>). The example lives in <Mono>packages/blueprint_schema/examples/</Mono>.</p>
          <Button variant="primary" icon="upload" onClick={() => setImporting(true)}>Import YAML</Button>
        </Card>
        {importModal}
      </>
    );
  }

  return (
    <>
      {header}
      <Card className="overflow-hidden mb-6">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-container-low text-left">
              {["Blueprint", "Latest version", "Applied on", "Tests", "Updated"].map((h) => (
                <th key={h} className="text-label uppercase text-text-secondary px-4 py-2.5 font-semibold">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {list.map((b) => (
              <tr key={b.name} onClick={() => setParams({ name: b.name })}
                className={`border-t border-hairline cursor-pointer ${b.name === selectedName ? "bg-container-low" : "hover:bg-surface"}`}>
                <td className="px-4 py-3">
                  <div className="font-semibold">{b.name}</div>
                  <div className="text-small text-text-secondary">{b.agents} agents · {b.workflows} workflow{b.workflows === 1 ? "" : "s"} · owner {b.owner}</div>
                </td>
                <td className="px-4 py-3"><span className="flex items-center gap-2"><Mono>v{b.latest}</Mono><StatusChip status={b.status} /></span></td>
                <td className="px-4 py-3 text-small">
                  {b.applied_on.length ? b.applied_on.map((a) => <div key={a.instance}>{a.instance} <span className="text-text-secondary">· v{a.version} applied {formatDate(a.at)}</span></div>)
                    : <span className="text-text-secondary">Not applied</span>}
                </td>
                <td className="px-4 py-3 text-small">{b.tests} tests<div className="text-text-secondary">Not run yet (Test Lab, Slice 3)</div></td>
                <td className="px-4 py-3 text-small">{timeAgo(b.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {selected && <VersionHistory key={selected.name} blueprint={selected} />}
      {importModal}
    </>
  );
}

function StatusChip({ status }: { status: string }) {
  return <Chip tone={status === "applied" ? "success" : status === "draft" ? "neutral" : "info"}>{status[0].toUpperCase() + status.slice(1)}</Chip>;
}

function VersionHistory({ blueprint }: { blueprint: BlueprintSummary }) {
  const { data: history } = useLoad(() => api.blueprintVersions(blueprint.name), [blueprint.name, blueprint.latest]);
  const [yamlFor, setYamlFor] = useState<number | null>(null);
  const [planFor, setPlanFor] = useState<number | null>(null);
  return (
    <Card className="overflow-hidden">
      <div className="flex items-end justify-between px-5 py-4 border-b border-hairline">
        <div>
          <h2 className="text-section m-0">Version history · {blueprint.name}</h2>
          <p className="text-small text-text-secondary m-0 mt-0.5">{blueprint.description ?? "Each version is immutable once applied."}</p>
        </div>
        <span className="text-small text-text-secondary">{blueprint.versions.length} version{blueprint.versions.length === 1 ? "" : "s"}</span>
      </div>
      {(history ?? []).map((v: BlueprintVersionInfo) => (
        <div key={v.version} className="flex items-center gap-6 px-5 py-3 border-t border-hairline first:border-t-0">
          <span className="w-28 flex items-center gap-2"><Mono>v{v.version}</Mono><StatusChip status={v.status} /></span>
          <span className="w-32 text-small">{formatDate(v.created_at)}</span>
          <span className="flex-1 text-small text-text-secondary">{v.author}</span>
          <Button onClick={() => setYamlFor(v.version)}>View YAML</Button>
          <Button variant="primary" icon="arrowRight" onClick={() => setPlanFor(v.version)}>Plan…</Button>
        </div>
      ))}
      {yamlFor !== null && <YamlModal name={blueprint.name} version={yamlFor} onClose={() => setYamlFor(null)} />}
      {planFor !== null && <PlanModal name={blueprint.name} version={planFor} onClose={() => setPlanFor(null)} />}
    </Card>
  );
}

function YamlModal({ name, version, onClose }: { name: string; version: number; onClose: () => void }) {
  const { data, error } = useLoad(() => api.blueprint(name, version), [name, version]);
  return (
    <Modal title={`${name} v${version}`} subtitle="Canonical YAML, as stored" width={820} onClose={onClose}>
      {error ? <Banner tone="error">{error}</Banner> : !data ? <Spinner /> : (
        <pre className="m-0 max-h-[60vh] overflow-auto p-4 rounded-control bg-container-low border border-hairline font-mono text-[12px] leading-[18px]">{data.yaml}</pre>
      )}
    </Modal>
  );
}

function PlanModal({ name, version, onClose }: { name: string; version: number; onClose: () => void }) {
  const navigate = useNavigate();
  const { data: instances } = useLoad(api.instances, []);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const choice = target || instances?.[0]?.id || "";

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const plan = await api.createPlan(name, version, choice);
      navigate(`/plans/${plan.id}`);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  return (
    <Modal title={`Plan ${name} v${version}`} subtitle="Computes the changes against the instance's imported live state. Nothing is applied yet." onClose={onClose}
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={!choice || busy} onClick={() => void create()}>{busy && <Spinner />}Create plan</Button>
      </>}>
      {!instances ? <Spinner /> : instances.length === 0 ? <Banner tone="info">Connect an instance first.</Banner> : (
        <Field label="Target instance" hint="Needs a recent import of live profiles (Instances → Import live profiles).">
          <select className={INPUT} value={choice} onChange={(e) => setTarget(e.target.value)}>
            {instances.map((i: Instance) => <option key={i.id} value={i.id}>{i.id} · {ENV_LABEL[i.environment]}{i.mode === "api-only" ? " · API only" : ""}</option>)}
          </select>
        </Field>
      )}
      {error && <Banner tone="error">{error}</Banner>}
    </Modal>
  );
}

function ImportModal({ onClose, onImported }: { onClose: () => void; onImported: (name: string) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function upload() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.uploadBlueprint(text);
      onImported(res.name);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  return (
    <Modal title="Import a blueprint" subtitle="Validated against Fleet Blueprint schema v1; secrets must be secret:// references." width={760} onClose={onClose}
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={!text.trim() || busy} onClick={() => void upload()}>{busy && <Spinner />}Validate and save</Button>
      </>}>
      <Label className="mb-1.5">YAML file</Label>
      <input type="file" accept=".yaml,.yml,.json" className="block mb-4 text-small"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) void f.text().then(setText); }} />
      <Label className="mb-1.5">Or paste</Label>
      <textarea className="w-full h-72 p-3 rounded-control border border-border font-mono text-[12px] outline-none focus:border-primary"
        value={text} onChange={(e) => setText(e.target.value)} placeholder="apiVersion: fleetcontrol/v1&#10;kind: Blueprint&#10;..." />
      {error && <Banner tone="error" className="mt-3">{error}</Banner>}
    </Modal>
  );
}
