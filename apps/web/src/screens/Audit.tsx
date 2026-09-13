import { useState } from "react";
import { AUDIT_EXPORT_URL, api } from "../api/client";
import { useLoad } from "../lib/hooks";
import { actorKind, auditLabel, formatDateTime, prettyId } from "../lib/view";
import { Banner, Card, INPUT, Icon, PageHeader, Spinner } from "../components/ui";

function Actor({ actor }: { actor: string }) {
  const kind = actorKind(actor);
  const bubble = "size-[22px] shrink-0 rounded-full flex items-center justify-center";
  if (kind === "agent") {
    return <span className="flex items-center gap-2 min-w-0"><span className={`${bubble} bg-secondary-tint text-secondary`}><Icon name="bot" size={13} /></span><span className="truncate">{actor.slice(6)} agent</span></span>;
  }
  if (kind === "system") {
    return <span className="flex items-center gap-2"><span className={`${bubble} bg-primary text-on-primary`}><Icon name="logo" size={12} /></span>Fleet Control</span>;
  }
  const initials = actor.split(/[@.\s]/)[0].slice(0, 2).toUpperCase();
  return <span className="flex items-center gap-2 min-w-0"><span className={`${bubble} bg-tertiary-tint text-tertiary text-[10px] font-bold`}>{initials}</span><span className="truncate">{actor}</span></span>;
}

const GRID = "grid grid-cols-[130px_190px_190px_minmax(0,1.6fr)_minmax(0,1.2fr)] gap-3 px-4";

export function AuditScreen() {
  const { data, error, loading } = useLoad(() => api.audit(2000), [], 10_000);
  const [query, setQuery] = useState("");
  const [actor, setActor] = useState("all");
  const [type, setType] = useState("all");

  const all = data ?? [];
  const actors = [...new Set(all.map((e) => e.actor))].sort();
  const types = [...new Set(all.map((e) => e.action.split(".")[0]))].sort();
  const q = query.trim().toLowerCase();
  const rows = all.filter((e) =>
    (actor === "all" || e.actor === actor) &&
    (type === "all" || e.action.startsWith(`${type}.`)) &&
    (!q || `${e.actor} ${auditLabel(e.action)} ${e.action} ${e.target} ${e.detail}`.toLowerCase().includes(q)));

  return (
    <>
      <PageHeader crumb="Govern" title="Audit log" subtitle="Every configuration change, plan, apply, approval and drift resolution, with who did it."
        actions={
          <a href={AUDIT_EXPORT_URL} download
            className="inline-flex items-center gap-2 h-control px-3.5 rounded-control border border-border bg-white text-text text-[13px] font-semibold no-underline hover:bg-container-low hover:text-text">
            <Icon name="download" />Export CSV
          </a>
        } />
      {error && !data && <Banner tone="error">{error}</Banner>}
      {loading && !data ? <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading the audit log…</div> : (
        <Card className="overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-3 border-b border-hairline">
            <input className={`${INPUT} max-w-[280px]`} placeholder="Filter…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Filter entries" />
            <select className={`${INPUT} max-w-[220px]`} value={actor} onChange={(e) => setActor(e.target.value)} aria-label="Actor">
              <option value="all">Actor: All</option>
              {actors.map((a) => <option key={a} value={a}>{actorKind(a) === "agent" ? `${a.slice(6)} agent` : a === "fleetcontrol" ? "Fleet Control" : a}</option>)}
            </select>
            <select className={`${INPUT} max-w-[180px]`} value={type} onChange={(e) => setType(e.target.value)} aria-label="Type">
              <option value="all">Type: All</option>
              {types.map((t) => <option key={t} value={t}>{prettyId(t)}</option>)}
            </select>
            <div className="flex-1" />
            <span className="text-small text-text-secondary">{rows.length === all.length ? `${all.length} entries` : `${rows.length} of ${all.length} entries`}</span>
          </div>
          <div className={`${GRID} py-2.5 bg-container-low text-label uppercase text-text-secondary`}>
            <div>When</div><div>Actor</div><div>Action</div><div>Target</div><div>Detail</div>
          </div>
          {rows.map((e) => (
            <div key={e.id} className={`${GRID} py-3 border-t border-hairline items-center text-[13px]`}>
              <div className="text-text-secondary num">{formatDateTime(e.ts)}</div>
              <Actor actor={e.actor} />
              <div className="font-medium" title={e.action}>{auditLabel(e.action)}</div>
              <div className="break-words">{e.target}</div>
              <div className="text-text-secondary break-words">{e.detail}</div>
            </div>
          ))}
          {rows.length === 0 && <div className="px-4 py-8 text-center text-text-secondary border-t border-hairline">{all.length ? "No entry matches the filters." : "Nothing recorded yet."}</div>}
        </Card>
      )}
    </>
  );
}
