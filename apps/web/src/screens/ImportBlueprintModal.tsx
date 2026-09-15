import { useState } from "react";
import { useNavigate } from "react-router";
import { api, type LiveImportResult } from "../api/client";
import { errorText, useLoad } from "../lib/hooks";
import { BLUEPRINT_NAME, suggestBlueprintName } from "../lib/names";
import { Banner, Button, Field, INPUT, Label, Modal, Mono, Spinner } from "../components/ui";

/** Blueprint v1 draft from what an instance runs today (its last import). Nothing on Hermes changes. */
export function ImportBlueprintModal({ instanceId, onClose }: { instanceId: string; onClose: () => void }) {
  const navigate = useNavigate();
  const { data: detail, error, loading } = useLoad(() => api.instance(instanceId), [instanceId]);
  const profiles = detail?.live_profiles ?? [];
  const [name, setName] = useState(() => suggestBlueprintName(instanceId));
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [done, setDone] = useState<LiveImportResult | null>(null);

  const chosen = profiles.filter((p) => !excluded.has(p));
  const nameOk = BLUEPRINT_NAME.test(name);

  function toggle(p: string) {
    const next = new Set(excluded);
    if (next.has(p)) next.delete(p);
    else next.add(p);
    setExcluded(next);
  }

  async function create() {
    setBusy(true);
    setFailure(null);
    try {
      setDone(await api.blueprintFromLive(instanceId, { name, profiles: chosen.length === profiles.length ? undefined : chosen }));
    } catch (e) {
      setFailure(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <Modal title="Blueprint created" onClose={onClose} footer={<>
        <Button onClick={onClose}>Close</Button>
        <Button variant="primary" onClick={() => navigate(`/blueprints?name=${encodeURIComponent(done.name)}`)}>Open in Blueprints</Button>
      </>}>
        <Banner tone="success">
          Saved <strong>{done.name} v{done.version}</strong> as a draft with {done.managed_profiles.length} agent
          {done.managed_profiles.length === 1 ? "" : "s"}. Planned against {instanceId} it changes nothing; edit it in Agent Studio, then plan.
        </Banner>
        {done.skipped.length > 0 && <>
          <Label className="mt-4 mb-2">Left out</Label>
          <ul className="m-0 pl-5 text-small flex flex-col gap-1">
            {done.skipped.map((s) => <li key={s.profile}><Mono>{s.profile}</Mono>: {s.reason}</li>)}
          </ul>
        </>}
      </Modal>
    );
  }

  return (
    <Modal width={560} title={`Create a blueprint from ${instanceId}`} onClose={onClose}
      subtitle="Describes what runs there today, from the last import. Nothing on Hermes changes."
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={() => void create()} disabled={busy || !nameOk || chosen.length === 0}>
          {busy && <Spinner />}Create draft
        </Button>
      </>}>
      {error && <Banner tone="error" className="mb-4">{error}</Banner>}
      <Field label="Blueprint name">
        <input className={INPUT} value={name} onChange={(e) => setName(e.target.value.trim())} aria-invalid={!nameOk} />
      </Field>
      {!nameOk && <p className="text-small text-warning mt-1 mb-0">Lowercase letters, digits and hyphens, starting with a letter.</p>}
      <Label className="mt-5 mb-2">Profiles ({chosen.length} of {profiles.length})</Label>
      {loading && !detail ? <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading live profiles…</div> : (
        <div className="border border-hairline rounded-control divide-y divide-hairline max-h-[260px] overflow-auto">
          {profiles.map((p) => (
            <label key={p} className="flex items-center gap-2.5 px-3 py-2 cursor-pointer">
              <input type="checkbox" aria-label={`Include ${p}`} className="accent-primary" checked={!excluded.has(p)} onChange={() => toggle(p)} />
              <Mono>{p}</Mono>
            </label>
          ))}
        </div>
      )}
      <p className="text-small text-text-secondary mt-2 mb-0">
        Profiles left out stay unmanaged: Fleet Control lists them on plans and never changes them.
      </p>
      {failure && <Banner tone="error" className="mt-4">{failure}</Banner>}
    </Modal>
  );
}
