import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { api, type ContentZone, type Workflow, type WorkflowStep } from "../api/client";
import { useAuth } from "../lib/auth";
import { errorText, useLoad, useNow } from "../lib/hooks";
import { RUN_STATUS, gateTimeout, stepSummary, titleCase } from "../lib/workflows";
import { timeAgo } from "../lib/view";
import { Banner, Button, Card, Chip, Field, INPUT, Icon, Modal, Mono, PageHeader, Spinner, TEXTAREA } from "../components/ui";

// design/screens/Workflows: the list, the steps, the step inspector; runs underneath.
export function WorkflowsScreen() {
  const { can } = useAuth();
  useNow(15_000);
  const { data: workflows, error } = useLoad(api.workflows, []);
  const { data: runs } = useLoad(() => api.workflowRuns(), [], 5000);
  const [selected, setSelected] = useState<string | null>(null);
  const [stepIndex, setStepIndex] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const current = workflows?.find((w) => `${w.blueprint}/${w.id}` === selected) ?? workflows?.[0] ?? null;
  const step = current?.steps.find((s) => s.index === stepIndex) ?? current?.steps.find((s) => s.kind === "human_gate") ?? current?.steps[0] ?? null;
  const theirs = (runs ?? []).filter((r) => current && r.blueprint === current.blueprint && r.workflow_id === current.id);

  return (
    <>
      <PageHeader crumb={current ? <>Design › {titleCase(current.blueprint)}</> : "Design"} title="Workflows"
        subtitle="Ordered steps with agents, expected artifacts and human gates. Human gates are decided in the portal; a reply in chat never is."
        actions={current && can("workflows.run") && <Button variant="primary" icon="flow" onClick={() => setRunning(true)}>Run</Button>} />
      {error && <Banner tone="error" className="mb-4">{error}</Banner>}
      {workflows && workflows.length === 0 && (
        <Card className="p-6 text-text-secondary">No applied blueprint has a workflow. Workflows are declared in a blueprint (<Mono>workflows:</Mono>) and run once it is applied.</Card>
      )}
      {current && (
        <div className="flex gap-5 items-start">
          <Card className="w-[240px] shrink-0 p-2 flex flex-col">
            <div className="px-2.5 pt-2 pb-1.5 text-label uppercase text-text-secondary">Workflows</div>
            {workflows?.map((w) => (
              <button key={`${w.blueprint}/${w.id}`} type="button" onClick={() => { setSelected(`${w.blueprint}/${w.id}`); setStepIndex(null); }}
                className={`text-left px-2.5 py-2 rounded-control cursor-pointer ${w === current ? "bg-primary-tint" : "bg-transparent hover:bg-container-low"}`}>
                <div className="text-[13px] font-medium">{titleCase(w.id)}</div>
                <div className="text-small text-text-secondary">{w.steps.length} steps{w.gates ? ` · ${w.gates} human gate${w.gates === 1 ? "" : "s"}` : ""} · {w.blueprint} v{w.version}</div>
              </button>
            ))}
          </Card>

          <div className="flex-1 min-w-0 flex flex-col gap-5">
            <Card className="p-[18px] flex flex-col gap-2">
              {current.steps.map((s) => <StepRow key={s.index} step={s} selected={s === step} onSelect={() => setStepIndex(s.index)} />)}
            </Card>
            <Card>
              <div className="flex items-baseline justify-between px-4 py-3.5 border-b border-hairline">
                <h2 className="text-section m-0">Runs</h2>
                <span className="text-small text-text-secondary">{theirs.length} run{theirs.length === 1 ? "" : "s"}</span>
              </div>
              {theirs.length === 0 && <p className="m-0 px-4 py-4 text-small text-text-secondary">Not run yet.</p>}
              {theirs.map((r) => (
                <Link key={r.id} to={`/workflow-runs/${r.id}`} className="grid grid-cols-[minmax(0,1fr)_160px_minmax(0,1fr)_110px] gap-3 px-4 py-3 border-b border-hairline last:border-b-0 items-center no-underline text-text hover:bg-container-low">
                  <span className="min-w-0">
                    <span className="block text-[13px] font-medium truncate">{r.case || r.id}</span>
                    <span className="text-small text-text-secondary">{r.started_by} · {timeAgo(r.started_at)} · on <Mono>{r.instance_id}</Mono></span>
                  </span>
                  <span><Chip tone={RUN_STATUS[r.status].tone}>{RUN_STATUS[r.status].label}</Chip></span>
                  <span className="text-small text-text-secondary truncate">
                    step {Math.min(r.progress.done + 1, r.progress.total)} of {r.progress.total}{r.progress.current_label ? `: ${r.progress.current_label}` : ""}
                    {r.overdue && <span className="text-error"> · overdue</span>}
                  </span>
                  <span className="text-small text-right">{r.room_id ? "room opened" : r.error ? <span className="text-error truncate">{r.error}</span> : ""}</span>
                </Link>
              ))}
            </Card>
          </div>

          {step && <Inspector workflow={current} step={step} />}
        </div>
      )}
      {running && current && <RunModal workflow={current} onClose={() => setRunning(false)} />}
    </>
  );
}

function StepRow({ step, selected, onSelect }: { step: WorkflowStep; selected: boolean; onSelect: () => void }) {
  const gate = step.kind === "human_gate";
  return (
    <button type="button" onClick={onSelect}
      className={`w-full text-left flex gap-3 items-start p-3 rounded-control border cursor-pointer ${selected ? "border-primary bg-primary-tint" : gate ? "border-warning/40 bg-warning-tint/30" : "border-hairline bg-white hover:bg-container-low"}`}>
      <span className="size-6 shrink-0 rounded-full bg-container-low text-[12px] font-semibold flex items-center justify-center">{step.index + 1}</span>
      <span className="min-w-0 flex-1">
        {step.kind === "parallel" ? (
          <>
            <span className="block text-[13px] font-semibold">Runs in parallel</span>
            {step.members?.map((m) => <span key={m.agent} className="block text-[13px]">{titleCase(m.agent)} <span className="text-text-secondary">→ {m.artifact ?? "result"}</span></span>)}
          </>
        ) : (
          <>
            <span className="block text-[13px] font-semibold">
              {gate ? "Human gate" : step.kind === "decision_room" ? "Output" : titleCase(step.members?.[0]?.agent ?? "")}
            </span>
            <span className="block text-small text-text-secondary">{gate ? `${titleCase(step.role ?? "")} review` : step.kind === "decision_room" ? "Decision Room opened" : stepSummary(step)}</span>
          </>
        )}
      </span>
      {gate && <Icon name="lock" className="text-warning shrink-0 mt-0.5" />}
    </button>
  );
}

function Inspector({ workflow, step }: { workflow: Workflow; step: WorkflowStep }) {
  const agent = step.members?.[0]?.agent;
  return (
    <Card className="w-[320px] shrink-0 p-[18px] flex flex-col gap-3">
      <div className="text-small text-text-secondary">Step {step.index + 1} · {step.kind === "human_gate" ? "Human gate" : step.kind === "parallel" ? "Parallel" : step.kind === "decision_room" ? "Output" : "Agent"}</div>
      <div className="text-[15px] font-semibold">{step.kind === "human_gate" ? `${titleCase(step.role ?? "")} review` : step.label}</div>
      <div className="border border-hairline rounded-control divide-y divide-hairline text-[13px]">
        {step.kind === "human_gate" && <>
          <Row label="Who">Any {titleCase(step.role ?? "")}, or an Admin</Row>
          <Row label="Timeout">{gateTimeout(step)}</Row>
          <Row label="On reject">{step.on_reject ? `Return to ${titleCase(step.on_reject)} with notes` : "The run stops"}</Row>
        </>}
        {(step.kind === "agent" || step.kind === "parallel") && step.members?.map((m) => (
          <Row key={m.agent} label={titleCase(m.agent)}>
            profile <Mono>{workflow.agents[m.agent]?.profile ?? m.agent}</Mono>
            {m.artifact && <> · produces <Mono>{m.artifact}</Mono></>}
            {(workflow.agents[m.agent]?.mcps.length ?? 0) > 0 && <> · uses {workflow.agents[m.agent].mcps.join(", ")}</>}
          </Row>
        ))}
        {step.kind === "decision_room" && <Row label="Question">{step.question_template ?? `Approve the outcome of ${workflow.id}?`}</Row>}
      </div>
      <p className="m-0 text-small text-text-secondary">
        {step.kind === "human_gate" ? "Approvals are recorded in the audit log. A person may also send the work back with a note."
          : agent ? "The agent reads the request and the full text of what earlier steps produced." : "Every artifact of the run is the room's evidence."}
      </p>
      <div className="border-t border-hairline pt-3">
        <div className="text-label uppercase text-text-secondary mb-1.5">Where it can run</div>
        {workflow.readiness.map((r) => (
          <div key={r.instance_id} className="text-[13px] py-0.5">
            <Icon name={r.ready ? "checkCircle" : "xCircle"} size={14} className={`${r.ready ? "text-success" : "text-error"} inline mr-1.5 -mt-0.5`} />
            <Mono>{r.instance_id}</Mono> <span className="text-text-secondary">({r.environment}{r.kanban?.dispatching ? ", Kanban" : ""})</span>
            {!r.ready && <div className="text-small text-text-secondary ml-5">{r.problems[0]}{r.problems.length > 1 ? ` (+${r.problems.length - 1} more)` : ""}</div>}
            {r.ready && (r.warnings ?? []).map((w) => <div key={w} className="text-small text-warning ml-5">{w}</div>)}
          </div>
        ))}
      </div>
    </Card>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="grid grid-cols-[96px_minmax(0,1fr)] gap-2 px-3 py-2.5"><span className="text-text-secondary">{label}</span><span className="min-w-0 break-words">{children}</span></div>;
}

function RunModal({ workflow, onClose }: { workflow: Workflow; onClose: () => void }) {
  const navigate = useNavigate();
  const { data: zones } = useLoad(api.contentZones, []);
  const ready = workflow.readiness.filter((r) => r.ready);
  const [instance, setInstance] = useState(ready.find((r) => r.environment === "lab")?.instance_id ?? ready[0]?.instance_id ?? "");
  const [input, setInput] = useState("");
  const [caseId, setCaseId] = useState("");
  const readable = (zones ?? []).filter((z: ContentZone) => z.may_read);
  const suggested = Object.values(workflow.agents).flatMap((a) => a.content_zones).find((z) => readable.some((r) => r.id === z));
  const [zone, setZone] = useState("");
  const [executor, setExecutor] = useState<"auto" | "runs" | "kanban">("auto");
  const kanban = ready.find((r) => r.instance_id === instance)?.kanban;
  const chosenZone = zone || suggested || readable[0]?.id || "";
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const run = await api.startWorkflow({ blueprint: workflow.blueprint, workflow_id: workflow.id, instance_id: instance, input: input.trim(),
                                            case: caseId.trim() || undefined, zone: chosenZone || undefined, executor });
      navigate(`/workflow-runs/${run.id}`);
    } catch (err) { setError(errorText(err)); setBusy(false); }
  }
  return (
    <Modal width={560} title={`Run ${titleCase(workflow.id)}`} onClose={onClose}
      subtitle="Each agent step runs on the instance through its Fleet Control Agent; the run stops at every human gate until someone decides it here."
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="wf-run" disabled={busy || !instance || input.trim().length < 3}>{busy && <Spinner />}Start run</Button>
      </>}>
      {ready.length === 0
        ? <Banner tone="warning">No instance can run it yet: {workflow.readiness[0]?.problems[0] ?? "no paired agent"}. Apply the blueprint there first.</Banner>
        : (
          <form id="wf-run" onSubmit={(e) => void submit(e)}>
            <Field label="Instance">
              <select className={INPUT} value={instance} onChange={(e) => setInstance(e.target.value)}>
                {ready.map((r) => <option key={r.instance_id} value={r.instance_id}>{r.instance_id} ({r.environment})</option>)}
              </select>
            </Field>
            {(ready.find((r) => r.instance_id === instance)?.warnings ?? []).map((w) => <Banner key={w} tone="warning" className="mb-3 text-small">{w}</Banner>)}
            <Field label="Request" hint="What the first step is asked; every later step also reads what came before.">
              <textarea className={TEXTAREA} rows={4} autoFocus value={input} maxLength={4000} onChange={(e) => setInput(e.target.value)}
                placeholder="Case AML-2026-0412: flagged transfer chain through a Limassol correspondent." />
            </Field>
            <Field label="Case" hint="Optional."><input className={INPUT} value={caseId} maxLength={64} onChange={(e) => setCaseId(e.target.value)} placeholder="AML-2026-0412" /></Field>
            <Field label="Runs as" hint={kanban?.dispatching
                ? "This instance's Hermes Kanban is dispatching: steps become linked tasks on its board. Gates stay here."
                : `Kanban is not available here${kanban?.why ? ` (${kanban.why})` : ""}: each agent step is a Hermes run.`}>
              <select className={INPUT} value={executor} onChange={(e) => setExecutor(e.target.value as "auto" | "runs" | "kanban")}>
                <option value="auto">Automatic ({kanban?.dispatching ? "Kanban" : "Hermes runs"})</option>
                <option value="runs">Hermes runs, one per step</option>
                <option value="kanban" disabled={!kanban?.dispatching}>Hermes Kanban board</option>
              </select>
            </Field>
            <Field label="Content zone" hint="Where its artifacts are kept and its Decision Room opens: who may read that zone sees them.">
              <select className={INPUT} value={chosenZone} onChange={(e) => setZone(e.target.value)}>
                {readable.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
              </select>
            </Field>
            {error && <Banner tone="error">{error}</Banner>}
          </form>
        )}
    </Modal>
  );
}
