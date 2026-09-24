import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, type FleetOutput, type OutputKind } from "../api/client";
import { useAuth } from "../lib/auth";
import { errorText, useLoad } from "../lib/hooks";
import { CLASSIFICATION_LABEL, CLASSIFICATION_TONE, formatSize } from "../lib/content";
import { KIND_ICON, KIND_LABEL, casesOf, matchesQuery } from "../lib/outputs";
import { formatDateTime } from "../lib/view";
import { Banner, Button, Card, Chip, INPUT, Icon, Modal, Mono, PageHeader, Spinner } from "../components/ui";

// design/screens/FleetOutputs: what the fleet has shared with your role
export function OutputsScreen() {
  const { can } = useAuth();
  const { data: outputs, error, reload } = useLoad(() => api.outputs({ limit: 200 }), [], 20_000);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<OutputKind | "">("");
  const [caseId, setCaseId] = useState("");
  const [search] = useSearchParams();
  const [open, setOpen] = useState<string | null>(search.get("id"));  // ?id= opens one output (Ask the fleet links here)
  const all = outputs ?? [];
  const rows = all.filter((o) => (!kind || o.kind === kind) && (!caseId || o.case === caseId) && matchesQuery(o, query));

  return (
    <>
      <PageHeader crumb="Workspace" title="Fleet outputs"
        subtitle="Documents and results the fleet has shared with your role. Each one says which agent produced it and from what." />
      {error && <Banner tone="error" className="mb-4">{error}</Banner>}

      <Card className="overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-hairline">
          <input className={`${INPUT} h-8 max-w-[280px]`} placeholder="Search outputs" value={query} onChange={(e) => setQuery(e.target.value)} />
          <select aria-label="Type" className={`${INPUT} h-8 w-[190px]`} value={kind} onChange={(e) => setKind(e.target.value as OutputKind | "")}>
            <option value="">Type: All</option>
            {(Object.keys(KIND_LABEL) as OutputKind[]).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
          </select>
          <select aria-label="Case" className={`${INPUT} h-8 w-[190px]`} value={caseId} onChange={(e) => setCaseId(e.target.value)}>
            <option value="">Case: All</option>
            {casesOf(all).map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div className="grid grid-cols-[2.4fr_1.1fr_1.2fr_1fr_1fr] gap-3 px-4 py-2.5 border-b border-hairline text-label uppercase text-text-secondary">
          <div>Output</div><div>Case</div><div>Produced by</div><div>When</div><div>Classification</div>
        </div>
        {!outputs && !error && <div className="px-4 py-4 flex gap-2 items-center text-text-secondary"><Spinner /> Loading outputs…</div>}
        {outputs && rows.length === 0 && (
          <p className="m-0 px-4 py-6 text-[13px] text-text-secondary">
            {all.length === 0
              ? <>Nothing yet. Outputs appear here when an agent files one, or when someone saves an answer. Zones decide what you see: see <Link to="/content">Content</Link>.</>
              : "No output matches the filters."}
          </p>
        )}
        {rows.map((o) => (
          <button key={o.id} type="button" onClick={() => setOpen(o.id)}
            className="w-full text-left grid grid-cols-[2.4fr_1.1fr_1.2fr_1fr_1fr] gap-3 px-4 py-3 border-b border-hairline items-center cursor-pointer hover:bg-surface">
            <span className="flex items-center gap-3 min-w-0">
              <Icon name={KIND_ICON[o.kind]} className="text-text-secondary shrink-0" />
              <span className="flex flex-col gap-0.5 min-w-0">
                <span className="font-medium truncate">{o.name}</span>
                <span className="text-small text-text-secondary">{KIND_LABEL[o.kind]}{o.size !== null ? ` · ${formatSize(o.size)}` : ""}</span>
              </span>
            </span>
            <span>{o.case ? <Mono className="text-[12px]">{o.case}</Mono> : <span className="text-outline">—</span>}</span>
            <span className="text-[13px] truncate">{o.produced_by}</span>
            <span className="text-[13px] text-text-secondary">{formatDateTime(o.at)}</span>
            <span><Chip tone={CLASSIFICATION_TONE[o.classification]}>{CLASSIFICATION_LABEL[o.classification]}</Chip></span>
          </button>
        ))}
        {rows.length > 0 && (
          <div className="px-4 py-2.5 text-small text-text-secondary">Showing {rows.length} of {all.length} · newest first</div>
        )}
      </Card>

      {open && <OutputModal id={open} canManage={can("content.manage")} onClose={() => setOpen(null)}
        onRemoved={async () => { setOpen(null); await reload(); }} />}
    </>
  );
}

function OutputModal({ id, canManage, onClose, onRemoved }: { id: string; canManage: boolean; onClose: () => void; onRemoved: () => Promise<void> }) {
  const { data: output, error } = useLoad(() => api.output(id), [id]);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function remove(o: FleetOutput) {
    if (!window.confirm(`Remove ${o.name} from ${o.zone}?`)) return;
    setBusy(true);
    setFailure(null);
    try {
      await api.deleteOutput(o.id);
      await onRemoved();
    } catch (e) {
      setFailure(errorText(e));
      setBusy(false);
    }
  }

  return (
    <Modal width={760} title={output?.name ?? "Output"} onClose={onClose}
      subtitle={output ? `${KIND_LABEL[output.kind]} · ${CLASSIFICATION_LABEL[output.classification]} · ${formatDateTime(output.at)}` : undefined}
      footer={<>
        {output && canManage && <Button variant="danger" disabled={busy} onClick={() => void remove(output)}>{busy && <Spinner />}Remove</Button>}
        <Button variant="primary" onClick={onClose}>Close</Button>
      </>}>
      {error && <Banner tone="error">{error}</Banner>}
      {failure && <Banner tone="error">{failure}</Banner>}
      {!output && !error && <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div>}
      {output && (
        <div className="flex flex-col gap-4">
          <div className="border border-hairline rounded-control divide-y divide-hairline">
            <Detail label="Provenance">{output.provenance}</Detail>
            <Detail label="Zone"><Mono>{output.zone}</Mono></Detail>
            {output.case && <Detail label="Case"><Mono>{output.case}</Mono></Detail>}
            {output.instance_id && <Detail label="Instance">{output.instance_id}</Detail>}
          </div>
          {output.text
            ? <pre className="m-0 p-3 bg-container-low rounded-control whitespace-pre-wrap break-words font-mono text-[12px] max-h-[420px] overflow-auto">{output.text}</pre>
            : <p className="m-0 text-text-secondary">This output was recorded without its content: the file itself stays where the fleet produced it.</p>}
        </div>
      )}
    </Modal>
  );
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[120px_minmax(0,1fr)] gap-3 px-3 py-2.5 text-[13px]">
      <span className="text-label uppercase text-text-secondary">{label}</span>
      <span className="min-w-0">{children}</span>
    </div>
  );
}
