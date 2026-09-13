import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { api, type BlueprintVersionInfo, type DriftAction, type ResolveResult } from "../api/client";
import { useAuth } from "../lib/auth";
import { errorText, useLoad } from "../lib/hooks";
import { driftRows, timeAgo, type DriftRow } from "../lib/view";
import { Banner, Button, Field, INPUT, Icon, Label, Modal, Mono, Spinner } from "../components/ui";

function defaultExpiry(): string {
  const d = new Date(Date.now() + 30 * 86_400_000);
  return d.toISOString().slice(0, 10);
}

const key = (r: DriftRow) => `${r.profile}|${r.field}`;

export function DriftModal({ instanceId, onClose }: { instanceId: string; onClose: () => void }) {
  const navigate = useNavigate();
  const { can } = useAuth();
  const { data: report, error, loading, reload } = useLoad(() => api.drift(instanceId), [instanceId]);
  const blueprint = report?.blueprint ?? null;
  const { data: versions } = useLoad<BlueprintVersionInfo[]>(
    () => (blueprint ? api.blueprintVersions(blueprint) : Promise.resolve([])),
    [blueprint],
  );
  const rows = driftRows(report?.drift);
  const [action, setAction] = useState<DriftAction | null>(null);
  const [unchecked, setUnchecked] = useState<Set<string>>(new Set());
  const [expiry, setExpiry] = useState(defaultExpiry);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [done, setDone] = useState<ResolveResult | null>(null);

  const chosen = rows.filter((r) => !unchecked.has(key(r)));
  const nextVersion = versions?.length ? Math.max(...versions.map((v) => v.version)) + 1 : (report?.version ?? 0) + 1;
  const hasMissing = chosen.some((r) => r.change === "missing");

  const options: { action: DriftAction; title: string; body: string; disabled?: string }[] = [
    { action: "accept", title: `Accept into blueprint (creates v${nextVersion}, draft)`,
      body: "The live values become the desired state. Apply the new version to make it the applied one.",
      disabled: !can("blueprints.write") ? "Accepting writes a new blueprint version, which your role cannot do."
        : hasMissing ? "A missing profile cannot be accepted; revert it or create an exception." : undefined },
    { action: "revert", title: `Revert ${instanceId} to the blueprint`,
      body: "Creates a plan that writes the blueprint values back. Nothing changes on Hermes until the plan is applied." },
    { action: "ignore_once", title: "Ignore once", body: "Clears this alert; the same drift is reported again on the next scan." },
    { action: "exception", title: "Create an exception (with expiry)",
      body: "Stops reporting these fields on this instance until the date you choose. Logged in the audit log." },
  ];

  function toggle(r: DriftRow) {
    const next = new Set(unchecked);
    if (next.has(key(r))) next.delete(key(r));
    else next.add(key(r));
    setUnchecked(next);
  }

  async function confirm() {
    if (!action) return;
    setBusy(true);
    setFailure(null);
    try {
      const fields = chosen.length === rows.length ? [] : chosen.map((r) => ({ profile: r.profile, field: r.field }));
      const extra = action === "exception" ? { expires_at: Date.parse(`${expiry}T23:59:59Z`) / 1000, reason: reason.trim() || undefined } : {};
      const res = await api.resolveDrift(instanceId, { action, fields, ...extra });
      if (res.action === "revert" && res.plan) {
        navigate(`/plans/${res.plan.id}`);
        return;
      }
      setDone(res);
      void reload();
    } catch (e) {
      setFailure(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const warnIcon = <span className="size-9 shrink-0 rounded-control bg-warning-tint text-warning flex items-center justify-center"><Icon name="warning" size={18} /></span>;

  if (loading && !report) {
    return <Modal title="Drift" onClose={onClose}><div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading the latest scan…</div></Modal>;
  }

  if (done) {
    const text =
      done.action === "accept" ? <>Saved <strong>{done.blueprint} v{done.version}</strong> as a draft. <Link to={`/blueprints?name=${done.blueprint}`}>Open it in Blueprints</Link> to plan and apply it.</>
      : done.action === "exception" ? <>Exception created until {new Date((done.expires_at ?? 0) * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}. Those fields are no longer reported on {instanceId}.</>
      : <>Alert cleared. The next scan reports this drift again if it is still there.</>;
    return (
      <Modal title="Drift resolved" onClose={onClose} footer={<Button variant="primary" onClick={onClose}>Close</Button>}>
        <Banner tone="success">{text}</Banner>
      </Modal>
    );
  }

  if (error || !report || rows.length === 0) {
    return (
      <Modal title={`${instanceId}: no open drift`} onClose={onClose} footer={<Button onClick={onClose}>Close</Button>}>
        {error ? <Banner tone="error">{error}</Banner> : report ? (
          <p className="m-0 text-text-secondary">Last scan {timeAgo(report.at)} against {report.blueprint} v{report.version} found nothing open.
            {report.exceptions?.length ? ` ${report.exceptions.length} field(s) are under an exception.` : ""}</p>
        ) : (
          <p className="m-0 text-text-secondary">No drift scan has run on this instance yet. Use Scan for drift on the Instances screen.</p>
        )}
      </Modal>
    );
  }

  return (
    <Modal width={720} icon={warnIcon} onClose={onClose}
      title={<>{instanceId} has drifted from {report.blueprint} v{report.version}</>}
      subtitle={`Detected ${timeAgo(report.at)} · ${rows.length} field${rows.length === 1 ? "" : "s"} changed outside Fleet Control`}
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={() => void confirm()} disabled={!action || busy || chosen.length === 0 || (action === "accept" && hasMissing)}>
          {busy && <Spinner />}Confirm
        </Button>
      </>}>
      <div className="flex flex-col gap-3 mb-6">
        {rows.map((r) => (
          <div key={key(r)} className="border border-hairline rounded-control overflow-hidden">
            <label className="flex items-center gap-2.5 px-3 py-2 bg-container-low border-b border-hairline cursor-pointer">
              <input type="checkbox" aria-label={`Resolve ${r.profile} ${r.label}`} className="accent-primary" checked={!unchecked.has(key(r))} onChange={() => toggle(r)} />
              <span className="font-semibold">{r.profile} › {r.label}</span>
            </label>
            <div className="grid grid-cols-2 divide-x divide-hairline">
              <div className="px-3 py-2.5">
                <Label className="mb-1">Blueprint</Label>
                <Mono>{r.blueprint}</Mono>
              </div>
              <div className="px-3 py-2.5">
                <Label className="mb-1">Live</Label>
                {r.change === "missing" ? <Mono className="bg-warning-tint text-warning px-1 rounded">missing on the instance</Mono>
                  : r.added.length || r.removed.length ? (
                    <div className="flex flex-wrap gap-1.5">
                      {r.added.map((x) => <Mono key={`+${x}`} className="bg-warning-tint text-warning px-1 rounded">{x} (added)</Mono>)}
                      {r.removed.map((x) => <Mono key={`-${x}`} className="bg-warning-tint text-warning px-1 rounded line-through">{x} (removed)</Mono>)}
                    </div>
                  ) : <span className="flex items-center gap-2"><Mono className="bg-warning-tint text-warning px-1 rounded">{r.live}</Mono><span className="text-small text-warning">changed</span></span>}
              </div>
            </div>
          </div>
        ))}
      </div>

      <Label className="mb-2">How should Fleet Control resolve this?</Label>
      <div className="flex flex-col gap-2">
        {options.map((o) => (
          <label key={o.action} title={o.disabled}
            className={`flex gap-3 p-3 rounded-control border ${o.disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"} ${action === o.action ? "border-primary bg-container-low" : "border-border"}`}>
            <input type="radio" name="resolution" aria-label={o.title} className="mt-1 accent-primary" disabled={Boolean(o.disabled)} checked={action === o.action} onChange={() => setAction(o.action)} />
            <span className="min-w-0 flex-1">
              <span className="font-semibold block">{o.title}</span>
              <span className="block text-small text-text-secondary mt-0.5">{o.disabled ?? o.body}</span>
              {o.action === "exception" && action === "exception" && (
                <span className="grid grid-cols-[180px_1fr] gap-3 mt-3">
                  <Field label="Until"><input type="date" className={INPUT} value={expiry} min={new Date().toISOString().slice(0, 10)} onChange={(e) => setExpiry(e.target.value)} /></Field>
                  <Field label="Reason (audit log)"><input className={INPUT} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Vendor hotfix, remove after upgrade" /></Field>
                </span>
              )}
            </span>
          </label>
        ))}
      </div>
      {failure && <Banner tone="error" className="mt-4">{failure}</Banner>}
    </Modal>
  );
}
