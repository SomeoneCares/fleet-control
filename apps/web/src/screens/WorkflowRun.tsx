import { useState } from "react";
import { Link, useParams } from "react-router";
import { api, type WorkflowRun, type WorkflowStep } from "../api/client";
import { useAuth } from "../lib/auth";
import { errorText, useLoad, useNow } from "../lib/hooks";
import { RUN_STATUS, STEP_STATUS, gateClock, gateTimeout, titleCase } from "../lib/workflows";
import { formatDateTime, timeAgo } from "../lib/view";
import { Banner, Button, Card, Chip, Mono, PageHeader, Spinner, TEXTAREA } from "../components/ui";

// One run, step by step: what each agent produced, who decided each gate, and the room it ended in.
export function WorkflowRunScreen() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const now = useNow(10_000);
  const { data: run, error, reload } = useLoad(() => api.workflowRun(id), [id], 4000);
  const [msg, setMsg] = useState<string | null>(null);
  if (error) return <Banner tone="error">{error}</Banner>;
  if (!run) return <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div>;
  const st = RUN_STATUS[run.status];
  const going = run.status === "running" || run.status === "waiting";
  return (
    <>
      <PageHeader crumb={<>{can("blueprints.read") ? <Link to="/workflows">Workflows</Link> : "Workflows"} › {titleCase(run.workflow_id)}</>}
        title={run.case || titleCase(run.workflow_id)}
        subtitle={<>{run.blueprint} v{run.version} on <Mono>{run.instance_id}</Mono> · {run.executor === "kanban" ? <>Kanban board <Mono>{run.board}</Mono></> : "Hermes runs"} · started by {run.started_by} {timeAgo(run.started_at)}{run.zone ? <> · zone <Mono>{run.zone}</Mono></> : null}</>}
        actions={<>
          <Chip tone={st.tone}>{st.label}</Chip>
          {going && can("workflows.run") && <Button variant="danger" onClick={async () => {
            if (!window.confirm("Stop this run? Steps in progress are abandoned; what they already produced stays.")) return;
            try { await api.cancelWorkflowRun(run.id); await reload(); } catch (e) { setMsg(errorText(e)); }
          }}>Stop run</Button>}
        </>} />
      {msg && <Banner tone="error" className="mb-4">{msg}</Banner>}
      {run.error && <Banner tone={run.status === "cancelled" ? "info" : "error"} className="mb-4">{run.error}</Banner>}
      {run.input && <Card className="p-4 mb-4"><div className="text-label uppercase text-text-secondary mb-1">Request</div><div className="whitespace-pre-wrap text-[13px]">{run.input}</div></Card>}
      {run.room_id && <Banner tone="success" className="mb-4">The run ended in a Decision Room: <Link to={`/rooms/${run.room_id}`}>open it</Link>. The decision is made there.</Banner>}
      <div className="flex flex-col gap-3">
        {run.steps.map((s) => <StepCard key={s.index} run={run} step={s} now={now} onChanged={reload} />)}
      </div>
    </>
  );
}

function StepCard({ run, step: s, now, onChanged }: { run: WorkflowRun; step: WorkflowStep; now: number; onChanged: () => Promise<void> }) {
  const st = STEP_STATUS[s.status];
  const title = s.kind === "human_gate" ? `${titleCase(s.role ?? "")} review` : s.kind === "decision_room" ? "Decision Room" : s.kind === "parallel" ? "Runs in parallel" : titleCase(s.members?.[0]?.agent ?? "");
  return (
    <Card className={`p-4 ${s.status === "waiting" ? "border-warning" : ""}`}>
      <div className="flex items-center gap-3">
        <span className="size-6 shrink-0 rounded-full bg-container-low text-[12px] font-semibold flex items-center justify-center">{s.index + 1}</span>
        <span className="text-[13px] font-semibold flex-1">{title}</span>
        {s.finished_at && <span className="text-small text-text-secondary">{formatDateTime(s.finished_at)}</span>}
        <Chip tone={st.tone}>{s.status === "running" ? <><Spinner /> {st.label}</> : st.label}</Chip>
      </div>
      {s.note && s.status !== "done" && <Banner tone="info" className="mt-3 text-small">Sent back with: {s.note}</Banner>}
      {s.error && <div className="mt-2 text-small text-error">{s.error}</div>}
      {(s.kind === "agent" || s.kind === "parallel") && (
        <div className="mt-3 flex flex-col gap-2">
          {s.members?.map((m) => {
            const r = s.results?.[m.agent];
            return (
              <details key={m.agent} className="border border-hairline rounded-control px-3 py-2" open={s.members!.length === 1 && !!r?.output && r.output.length < 1500}>
                <summary className="cursor-pointer text-[13px] flex items-center gap-2">
                  <span className="font-medium">{titleCase(m.agent)}</span>
                  {m.artifact && <Mono className="text-text-secondary">{m.artifact}</Mono>}
                  {s.tasks?.[m.agent] && s.tasks[m.agent] !== "submitting" && <Mono className="text-[11px] text-text-secondary">task {s.tasks[m.agent]}</Mono>}
                  <span className="ml-auto text-small text-text-secondary">
                    {r ? (r.ok ? `done${r.tools ? ` · ${r.tools.length ? "called " + r.tools.join(", ") : "called no tools"}` : ""}` : `failed: ${r.error}`)
                      : s.tasks?.[m.agent] === "submitting" ? "submitting to Kanban…" : s.tasks?.[m.agent] ? "on the Kanban board" : s.status === "running" ? "working…" : ""}
                  </span>
                </summary>
                {r?.output && <pre className="mt-2 mb-0 p-3 bg-container-low rounded-control whitespace-pre-wrap break-words font-mono text-[12px] max-h-[320px] overflow-auto">{r.output}</pre>}
                {r?.artifact_id && <div className="mt-1.5 text-small"><Link to={`/outputs?id=${r.artifact_id}`}>Kept in Fleet outputs</Link></div>}
              </details>
            );
          })}
        </div>
      )}
      {s.kind === "human_gate" && <Gate run={run} step={s} now={now} onChanged={onChanged} />}
      {s.kind === "decision_room" && s.room_id && s.room_id !== "pending" && <div className="mt-2 text-[13px]"><Link to={`/rooms/${s.room_id}`}>Open the room</Link></div>}
    </Card>
  );
}

function Gate({ run, step: s, now, onChanged }: { run: WorkflowRun; step: WorkflowStep; now: number; onChanged: () => Promise<void> }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const may = run.may_decide[String(s.index)];
  const clock = s.status === "waiting" ? gateClock(s, now) : null;
  async function decide(approve: boolean) {
    setBusy(true); setError(null);
    try { await api.decideWorkflowGate(run.id, s.index, { approve, note: note.trim() }); setNote(""); await onChanged(); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return (
    <div className="mt-3 flex flex-col gap-2 text-[13px]">
      <div className="text-text-secondary">
        {gateTimeout(s)}{s.on_reject ? ` · sending back returns to ${titleCase(s.on_reject)}` : ""}
        {clock && <span className={clock.late ? "text-error" : ""}> · {clock.text}</span>}
        {s.escalated_at && <span className="text-warning"> · escalated {timeAgo(s.escalated_at)}</span>}
      </div>
      {s.gate && <div>{s.gate.approved ? "Approved" : "Sent back"} by {s.gate.by} {timeAgo(s.gate.at)}{s.gate.note ? `: “${s.gate.note}”` : ""}</div>}
      {s.status === "waiting" && (may?.allowed ? (
        <>
          <textarea className={TEXTAREA} rows={2} value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)}
            placeholder="A note: required to send the work back, recorded either way." />
          <div className="flex gap-2">
            <Button variant="primary" disabled={busy} onClick={() => void decide(true)}>{busy && <Spinner />}Approve</Button>
            <Button disabled={busy || !note.trim()} onClick={() => void decide(false)}>Send back{s.on_reject ? ` to ${titleCase(s.on_reject)}` : " (stops the run)"}</Button>
          </div>
        </>
      ) : <div className="text-text-secondary">{may?.why ?? "You cannot decide this gate."}</div>)}
      {error && <Banner tone="error">{error}</Banner>}
    </div>
  );
}
