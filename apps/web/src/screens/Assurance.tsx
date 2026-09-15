import { useState } from "react";
import { Link } from "react-router";
import { api, type ClaimRow, type Verdict } from "../api/client";
import { useAuth } from "../lib/auth";
import { errorText, useLoad, useNow } from "../lib/hooks";
import { VERDICT_MEANING, VERDICT_TONE } from "../lib/testlab";
import { formatDateTime, timeAgo } from "../lib/view";
import { Banner, Button, Card, Chip, INPUT, Icon, KpiTile, Mono, PageHeader, Spinner } from "../components/ui";
import { RunDetail } from "./TestLab";

const VERDICTS: Verdict[] = ["Evidence found", "No evidence", "Not verifiable", "Policy blocked"];

// design/screens/Assurance: claims · evidence rail
export function AssuranceScreen() {
  const now = useNow();
  const [verdict, setVerdict] = useState<Verdict | "">("");
  const [instance, setInstance] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const { data: summary } = useLoad(api.assuranceSummary, [], 15_000);
  const { data: claims, error } = useLoad(() => api.assuranceClaims({ verdict: verdict || undefined, instance_id: instance || undefined }),
    [verdict, instance], 15_000);
  const all = claims ?? [];
  const q = query.trim().toLowerCase();
  const shown = q ? all.filter((c) => `${c.claim} ${c.agent} ${c.test_id} ${c.run_id}`.toLowerCase().includes(q)) : all;
  const current = shown.find((c) => c.id === selected) ?? shown[0] ?? null;
  const instances = [...new Set(all.map((c) => c.instance_id))].sort();
  const evidenceShare = summary?.total ? Math.round((summary.counts["Evidence found"] / summary.total) * 100) : null;

  return (
    <>
      <PageHeader crumb="Operate" title="Assurance"
        subtitle="Every claim is checked against what actually ran: the tool calls in the Hermes session transcript. It shows whether the run did what was claimed, not whether the content is right."
        actions={<a href="/api/v1/assurance/export" download className="no-underline"><Button icon="download">Export evidence (CSV)</Button></a>} />

      <div className="grid grid-cols-3 gap-4 mb-5">
        <KpiTile label="Claims checked (24h)" value={summary ? summary.total.toLocaleString("en-GB") : "—"}
          note={summary ? `Across ${summary.instances} instance${summary.instances === 1 ? "" : "s"}${evidenceShare !== null ? ` · ${evidenceShare}% with evidence` : ""}` : "Loading…"} />
        <KpiTile label="No evidence" value={summary?.counts["No evidence"] ?? "—"} unit="claims" tone={summary?.counts["No evidence"] ? "error" : "success"}
          note={summary?.counts["No evidence"] ? "Needs review" : "Nothing claimed that did not run"} />
        <KpiTile label="Not verifiable" value={summary?.counts["Not verifiable"] ?? "—"} unit="claims"
          note={summary?.most_not_verifiable ? `Mostly on ${summary.most_not_verifiable}: its runs came back without a transcript` : "Every run had a transcript"} />
      </div>

      <div className="flex gap-5 items-start">
        <Card className="flex-1 min-w-0 overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-3 border-b border-hairline">
            <input className={`${INPUT} h-8 max-w-[260px]`} placeholder="Filter claims, agents, runs" value={query} onChange={(e) => setQuery(e.target.value)} />
            <select aria-label="Instance" className={`${INPUT} h-8 w-[200px]`} value={instance} onChange={(e) => setInstance(e.target.value)}>
              <option value="">Instance: All</option>
              {instances.map((i) => <option key={i} value={i}>{i}</option>)}
            </select>
            <select aria-label="Verdict" className={`${INPUT} h-8 w-[190px]`} value={verdict} onChange={(e) => setVerdict(e.target.value as Verdict | "")}>
              <option value="">Verdict: All</option>
              {VERDICTS.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
          <div className="grid grid-cols-[1fr_1.2fr_2.6fr_1.2fr] gap-3 px-4 py-2.5 border-b border-hairline text-label uppercase text-text-secondary">
            <div>Run</div><div>Agent</div><div>Claim</div><div>Verdict</div>
          </div>
          {error && <Banner tone="error" className="m-4">{error}</Banner>}
          {!claims && !error && <div className="px-4 py-4 flex gap-2 items-center text-text-secondary"><Spinner /> Loading claims…</div>}
          {claims && shown.length === 0 && (
            <p className="m-0 px-4 py-6 text-text-secondary text-[13px]">
              No claims {verdict || instance || q ? "match the filters" : "yet"}. Claims come from test runs: run a suite in <Link to="/testlab">Test Lab</Link>.
            </p>
          )}
          {shown.slice(0, 200).map((c) => (
            <button key={c.id} type="button" onClick={() => setSelected(c.id)}
              className={`w-full text-left grid grid-cols-[1fr_1.2fr_2.6fr_1.2fr] gap-3 px-4 py-3 border-b border-hairline items-center cursor-pointer ${current?.id === c.id ? `bg-container-low ${c.verdict === "No evidence" ? "shadow-[inset_3px_0_0_var(--color-error)]" : ""}` : "hover:bg-surface"}`}>
              <span className="flex flex-col gap-0.5 min-w-0"><Mono className="text-[12px] truncate">{c.run_id}</Mono><span className="text-small text-text-secondary">{timeAgo(c.at, now)}</span></span>
              <span className="text-[13px] truncate">{c.agent}</span>
              <span className="text-[13px] flex flex-col gap-0.5 min-w-0"><span>{c.claim}</span><span className="text-small text-text-secondary truncate">{c.instance_id} · {c.detail}</span></span>
              <span><Chip tone={VERDICT_TONE[c.verdict]}>{c.verdict}</Chip></span>
            </button>
          ))}
          {shown.length > 0 && (
            <div className="px-4 py-2.5 text-small text-text-secondary">Showing {Math.min(shown.length, 200)} of {shown.length} claims · last 7 days</div>
          )}
        </Card>
        {current && <EvidenceRail key={current.id} claim={current} />}
      </div>
    </>
  );
}

function EvidenceRail({ claim }: { claim: ClaimRow }) {
  const { can } = useAuth();
  const { data: run, error } = useLoad(() => api.testRun(claim.run_id), [claim.run_id]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function rerun() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.startTestRuns({ blueprint: claim.blueprint, version: claim.version, instance_id: claim.instance_id, test_ids: [claim.test_id] });
      setMsg(r.runs.length ? `Queued on ${claim.instance_id}; the result appears here and in Test Lab.` : r.skipped[0]?.reason ?? "Nothing queued.");
    } catch (e) {
      setMsg(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="w-[400px] shrink-0 p-[18px] flex flex-col gap-4">
      <div>
        <div className="flex justify-between items-center gap-3">
          <span className="text-[15px] font-semibold">Evidence · <Mono className="text-[13px] font-medium">{claim.run_id}</Mono></span>
          <Chip tone={VERDICT_TONE[claim.verdict]}>{claim.verdict}</Chip>
        </div>
        <div className="text-small text-text-secondary mt-0.5">{claim.agent} · {claim.instance_id} · {formatDateTime(claim.at)}</div>
      </div>
      <div>
        <div className="text-label uppercase text-text-secondary mb-2">Claim</div>
        <div className="border-l-[3px] border-border pl-3 text-[14px] leading-5 italic">“{claim.claim}”</div>
        <div className="text-small text-text-secondary mt-1.5">{claim.detail}</div>
      </div>
      <div className={`rounded-control px-3.5 py-3 flex gap-2.5 items-start text-[13px] leading-[18px] ${claim.verdict === "No evidence" ? "bg-error-tint text-error" : claim.verdict === "Policy blocked" ? "bg-warning-tint text-warning" : "bg-container-low"}`}>
        <Icon name={claim.verdict === "Evidence found" ? "checkCircle" : claim.verdict === "Not verifiable" ? "clock" : "warning"} size={16} className="shrink-0 mt-0.5" />
        <span><strong className="font-semibold">{claim.verdict}:</strong> {VERDICT_MEANING[claim.verdict]}</span>
      </div>
      {error && <Banner tone="error">{error}</Banner>}
      {!run && !error && <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading the run…</div>}
      {run && <RunDetail run={run} />}
      <div className="flex flex-col gap-2">
        {can("tests.run") && <Button icon="refresh" disabled={busy} onClick={() => void rerun()}>{busy && <Spinner />}Re-run this test</Button>}
        <Link to={`/testlab?blueprint=${encodeURIComponent(claim.blueprint)}&test=${encodeURIComponent(claim.test_id)}`} className="no-underline">
          <Button icon="flask" className="w-full justify-center">Open in Test Lab</Button>
        </Link>
      </div>
      {msg && <Banner tone="info">{msg}</Banner>}
      <div className="text-small text-text-secondary">Checked by test <Mono>{claim.test_id}</Mono> of {claim.blueprint} v{claim.version}.</div>
    </Card>
  );
}
