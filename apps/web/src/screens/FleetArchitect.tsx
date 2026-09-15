import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router";
import {
  api, type AgentDecisionValue, type ArchitectConfigDoc, type ArchitectConstraints, type ArchitectSessionSummary,
  type ProposalVersion, type ProposedAgent,
} from "../api/client";
import { useAuth } from "../lib/auth";
import {
  DEFAULT_CONSTRAINTS, EXAMPLE_MISSIONS, MISSION_MAX, MISSION_MIN, agentName, blueprintNameFor, constraintSummary, decisionCounts,
  effectiveAgent, fleetShape,
} from "../lib/architect";
import { errorText, useLoad, useNow } from "../lib/hooks";
import { BLUEPRINT_NAME } from "../lib/names";
import { formatDate, timeAgo } from "../lib/view";
import { Banner, Button, Card, Chip, Field, INPUT, Icon, KpiTile, Modal, Mono, PageHeader, Spinner, TEXTAREA, TagInput } from "../components/ui";

const SUBTITLE = "Describe the mission; an architect profile proposes a fleet. Nothing is applied until you review a plan.";
const CHIP = "inline-flex items-center h-[22px] px-2 rounded-full text-[12px]";

// design/screens/FleetArchitectEmpty (first run) and FleetArchitect (proposal)
export function FleetArchitectScreen() {
  const { can } = useAuth();
  const writer = can("blueprints.write");
  const { data: config, reload: reloadConfig } = useLoad(api.architectConfig, []);
  const { data: sessions, reload: reloadSessions } = useLoad(api.architectSessions, []);
  const [selected, setSelected] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);
  const current = composing ? null : selected ?? sessions?.[0]?.id ?? null;

  const history = sessions && sessions.length > 0 ? (
    <>
      <select aria-label="Architect sessions" className={`${INPUT} h-9 w-[260px]`} value={current ?? ""}
        onChange={(e) => { setComposing(false); setSelected(e.target.value || null); }}>
        {composing && <option value="">New mission</option>}
        {sessions.map((s) => <option key={s.id} value={s.id}>{sessionLabel(s)}</option>)}
      </select>
      {writer && !composing && <Button icon="plus" onClick={() => setComposing(true)}>New mission</Button>}
    </>
  ) : null;

  if (!sessions || !config) {
    return (
      <>
        <PageHeader crumb="Design" title="Fleet Architect" subtitle={SUBTITLE} />
        <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div>
      </>
    );
  }
  if (!current) {
    return (
      <Composer config={config} writer={writer} history={history} onConfigChanged={() => void reloadConfig()}
        onStarted={(id) => { setComposing(false); setSelected(id); void reloadSessions(); }} />
    );
  }
  return (
    <SessionView key={current} sid={current} config={config} writer={writer} history={history}
      onConfigChanged={() => void reloadConfig()} onChanged={() => void reloadSessions()} />
  );
}

function sessionLabel(s: ArchitectSessionSummary): string {
  const text = s.mission.length > 38 ? `${s.mission.slice(0, 38)}…` : s.mission;
  return `${text} · ${formatDate(s.created_at)}`;
}

// ---------------------------------------------------------------- first run: the mission

function Composer({ config, writer, history, onStarted, onConfigChanged }: {
  config: ArchitectConfigDoc; writer: boolean; history: ReactNode; onStarted: (id: string) => void; onConfigChanged: () => void;
}) {
  const [mission, setMission] = useState("");
  const [constraints, setConstraints] = useState<ArchitectConstraints>(DEFAULT_CONSTRAINTS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const long = mission.trim().length >= MISSION_MIN;

  async function propose() {
    setBusy(true);
    setError(null);
    try {
      const s = await api.startArchitect({ mission: mission.trim(), constraints });
      onStarted(s.id);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  const hint = !writer ? "Admins and Fleet Architects ask the architect; your role can read proposals."
    : !config.config ? "Choose the architect profile first."
      : !long ? `At least ${MISSION_MIN} characters.`
        : <>Uses <strong>{config.config.profile}</strong>{config.config.model ? ` · ${config.config.model.name}` : ""} on {config.config.instance_id} · usually a minute or two</>;

  return (
    <>
      <PageHeader crumb="Design" title="Fleet Architect" subtitle={SUBTITLE} actions={history} />
      <div className="flex justify-center pt-6">
        <div className="w-full max-w-[720px] flex flex-col gap-5">
          <div className="flex flex-col items-center text-center gap-1.5">
            <span className="size-10 rounded-card bg-secondary-tint text-secondary flex items-center justify-center mb-1.5"><Icon name="spark" size={20} /></span>
            <h2 className="m-0 text-[20px] leading-7 font-semibold">What should this fleet do?</h2>
            <p className="m-0 text-text-secondary">Plain language is fine. The architect turns it into agents, skills and tests you can edit.</p>
          </div>

          <div className="bg-white border border-border rounded-card flex flex-col">
            <textarea aria-label="Mission" className="min-h-[180px] px-4 py-4 text-[14px] leading-[22px] outline-none resize-y rounded-t-card bg-transparent"
              maxLength={MISSION_MAX} placeholder="Describe the mission, who it serves, what it must never do…" value={mission}
              onChange={(e) => setMission(e.target.value)} />
            <div className="flex justify-between items-center px-4 py-2.5 border-t border-hairline text-small text-text-secondary">
              <span className="flex items-center gap-2"><Icon name="lock" size={14} />Sent only to the architect profile on your own instance</span>
              <span className="num">{mission.length.toLocaleString("en-GB")} / 2,000</span>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-small text-text-secondary">Try an example:</span>
            {EXAMPLE_MISSIONS.map((ex) => (
              <button key={ex.label} type="button" onClick={() => setMission(ex.text)}
                className="h-7 px-3 rounded-full border border-border bg-white text-small font-medium cursor-pointer hover:bg-container-low">{ex.label}</button>
            ))}
          </div>

          <ConstraintsEditor value={constraints} onChange={setConstraints} />
          <ArchitectCard config={config} writer={writer} onChanged={onConfigChanged} />
          {error && <Banner tone="error">{error}</Banner>}

          <div className="flex items-center gap-3">
            <Button variant="primary" icon="spark" disabled={!writer || !config.config || !long || busy} onClick={() => void propose()}>
              {busy && <Spinner />}Propose a fleet
            </Button>
            <span className="text-small text-text-secondary">{hint}</span>
          </div>

          <ol className="grid grid-cols-3 gap-4 list-none p-0 m-0 mt-2 pt-5 border-t border-hairline">
            {["The architect proposes agents, skills and tests", "You review and edit the blueprint", "A plan shows every change before anything is applied"].map((s, n) => (
              <li key={s} className="flex gap-2.5 items-start">
                <span className="size-[22px] shrink-0 rounded-full bg-primary-tint text-primary text-[12px] font-bold flex items-center justify-center">{n + 1}</span>
                <span className="text-[13px] leading-[18px] text-text-secondary pt-0.5">{s}</span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </>
  );
}

function ConstraintsEditor({ value, onChange }: { value: ArchitectConstraints; onChange: (v: ArchitectConstraints) => void }) {
  const row = "flex justify-between items-center gap-3 px-3 py-2 text-[13px]";
  return (
    <Card className="p-[18px]">
      <div className="text-[15px] font-semibold mb-3">Constraints</div>
      <div className="border border-hairline rounded-control divide-y divide-hairline">
        <div className={row}>
          <span>Data residency</span>
          <select aria-label="Data residency" className={`${INPUT} h-8 w-[220px]`} value={value.data_residency}
            onChange={(e) => onChange({ ...value, data_residency: e.target.value as ArchitectConstraints["data_residency"] })}>
            <option value="any">No restriction</option>
            <option value="region">Within the region</option>
            <option value="on-premises">On-premises only</option>
          </select>
        </div>
        <div className={row}>
          <span>Cloud models</span>
          <select aria-label="Cloud models" className={`${INPUT} h-8 w-[220px]`} value={value.cloud_models}
            onChange={(e) => onChange({ ...value, cloud_models: e.target.value as ArchitectConstraints["cloud_models"] })}>
            <option value="allowed">Allowed</option>
            <option value="redacted-only">Redacted summaries only</option>
            <option value="none">Not allowed (local only)</option>
          </select>
        </div>
        <div className={row}>
          <span>External actions</span>
          <label className="flex items-center gap-2 text-small font-medium cursor-pointer">
            <input type="checkbox" className="accent-primary" checked={value.external_actions_need_approval}
              onChange={(e) => onChange({ ...value, external_actions_need_approval: e.target.checked })} />
            Require human approval
          </label>
        </div>
        <div className={row}>
          <span>Budget</span>
          <span className="flex items-center gap-2">
            <span className="text-small">$</span>
            <input type="number" min={0} aria-label="Budget per day in dollars" className={`${INPUT} h-8 w-24`} placeholder="none"
              value={value.budget_usd_per_day ?? ""}
              onChange={(e) => onChange({ ...value, budget_usd_per_day: e.target.value === "" ? null : Number(e.target.value) })} />
            <span className="text-small text-text-secondary">a day</span>
          </span>
        </div>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------- the architect profile

function ArchitectCard({ config, writer, onChanged }: { config: ArchitectConfigDoc; writer: boolean; onChanged: () => void }) {
  const [picking, setPicking] = useState(false);
  const c = config.config;
  return (
    <Card className="p-[18px] flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-[15px] font-semibold">Architect</span>
        {writer && <Button onClick={() => setPicking(true)}>{c ? "Change" : "Choose"}</Button>}
      </div>
      {c ? (
        <div className="flex items-center gap-3 border border-hairline rounded-control px-3 py-2.5">
          <span className="size-8 shrink-0 rounded-control bg-secondary-tint text-secondary flex items-center justify-center"><Icon name="spark" /></span>
          <div className="min-w-0">
            <div className="text-[13px] font-semibold truncate">
              {c.profile} <span className="text-outline font-normal">·</span> {c.instance_id}
              {c.model && <> <span className="text-outline font-normal">·</span> {c.model.name}</>}
            </div>
            <div className="text-small text-text-secondary">Hermes profile, asked through the Fleet Control Agent; it only proposes</div>
          </div>
        </div>
      ) : (
        <p className="m-0 text-small text-text-secondary">
          No architect yet. Choose a profile on a connected instance, or create the Fleet Control architect blueprint and apply it first.
        </p>
      )}
      {c && c.instance_status !== "healthy" && <Banner tone="warning" className="text-small">{c.instance_id} is {c.instance_status}; the request waits until its agent reports.</Banner>}
      <div className="text-small text-text-secondary">Returns a structured proposal (<Mono>fleetcontrol.proposal/v1</Mono>), not free text.</div>
      {picking && <PickArchitectModal config={config} onClose={() => setPicking(false)} onSaved={() => { setPicking(false); onChanged(); }} />}
    </Card>
  );
}

function PickArchitectModal({ config, onClose, onSaved }: { config: ArchitectConfigDoc; onClose: () => void; onSaved: () => void }) {
  const navigate = useNavigate();
  const candidates = config.candidates;
  const [instanceId, setInstanceId] = useState(config.config?.instance_id ?? candidates[0]?.instance_id ?? "");
  const [profile, setProfile] = useState(config.config?.profile ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inst = candidates.find((c) => c.instance_id === instanceId);

  async function run(key: string, fn: () => Promise<void>) {
    setBusy(key);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errorText(e));
      setBusy(null);
    }
  }

  return (
    <Modal title="Choose the architect" onClose={onClose}
      subtitle="A Hermes profile that turns missions into proposals, asked through the Fleet Control Agent on its instance."
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={!instanceId || !profile || busy !== null}
          onClick={() => void run("save", async () => { await api.setArchitectConfig({ instance_id: instanceId, profile }); onSaved(); })}>
          {busy === "save" && <Spinner />}Use this profile
        </Button>
      </>}>
      {candidates.length === 0 ? (
        <Banner tone="warning">No instance has a paired Fleet Control Agent and imported profiles yet. Connect one on Instances and import its live profiles.</Banner>
      ) : (
        <>
          <Field label="Instance">
            <select className={INPUT} value={instanceId} onChange={(e) => { setInstanceId(e.target.value); setProfile(""); }}>
              {candidates.map((c) => <option key={c.instance_id} value={c.instance_id}>{c.instance_id} ({c.environment})</option>)}
            </select>
          </Field>
          <Field label="Profile" hint="Any profile works: the proposal contract travels with every request. A dedicated one keeps architect sessions out of your agents' history.">
            <select className={INPUT} value={profile} onChange={(e) => setProfile(e.target.value)}>
              <option value="">Choose a profile…</option>
              {inst?.profiles.map((p) => <option key={p.name} value={p.name}>{p.name}{p.model ? ` · ${p.model}` : ""}</option>)}
            </select>
          </Field>
          <div className="border-t border-hairline pt-4 mt-2">
            <div className="text-[13px] font-semibold">Or use the Fleet Control architect</div>
            <p className="text-small text-text-secondary mt-1 mb-3">
              Creates the <Mono>fleet-control-architect</Mono> blueprint: one <Mono>fc-architect</Mono> profile with no skills and no tools, on
              this instance's default model. Plan and apply it, import live profiles, then choose <Mono>fc-architect</Mono> here.
            </p>
            <Button icon="layers" disabled={!instanceId || busy !== null}
              onClick={() => void run("asset", async () => {
                const r = await api.architectAsset(instanceId);
                navigate(`/blueprints?name=${encodeURIComponent(r.name)}`);
              })}>
              {busy === "asset" && <Spinner />}Create the architect blueprint
            </Button>
          </div>
        </>
      )}
      {error && <Banner tone="error" className="mt-4">{error}</Banner>}
    </Modal>
  );
}

// ---------------------------------------------------------------- a session: proposal, decisions, questions

function SessionView({ sid, config, writer, history, onConfigChanged, onChanged }: {
  sid: string; config: ArchitectConfigDoc; writer: boolean; history: ReactNode; onConfigChanged: () => void; onChanged: () => void;
}) {
  const now = useNow(5000);
  const { data: s, error, reload } = useLoad(() => api.architectSession(sid), [sid], 4000);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [editing, setEditing] = useState<ProposedAgent | null>(null);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function act(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    setFailure(null);
    try {
      await fn();
      await reload();
      onChanged();
    } catch (e) {
      setFailure(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  if (!s) {
    return (
      <>
        <PageHeader crumb="Design" title="Fleet Architect" subtitle={SUBTITLE} actions={history} />
        {error ? <Banner tone="error">{error}</Banner> : <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div>}
      </>
    );
  }

  const latest = s.latest;
  const proposal = latest?.proposal ?? null;
  const last = s.versions[s.versions.length - 1];
  const counts = decisionCounts(s);
  const questions = proposal?.open_questions ?? [];
  const given = questions.map((q, i) => ({ question: q, answer: (answers[i] ?? "").trim() })).filter((a) => a.answer);

  return (
    <>
      <PageHeader crumb="Design" title="Fleet Architect"
        subtitle={`Asked ${s.architect.profile} on ${s.architect.instance_id}. Nothing is applied until you review a plan.`}
        actions={<>
          {history}
          {writer && <>
            <Button icon="refresh" disabled={Boolean(s.pending) || busy !== null}
              onClick={() => void act("ask", async () => { await api.askArchitect(sid, { answers: given }); setAnswers({}); })}>
              {busy === "ask" && <Spinner />}Ask again{given.length ? ` (${given.length} answer${given.length === 1 ? "" : "s"})` : ""}
            </Button>
            <Button variant="primary" icon="layers" disabled={!counts.accepted || busy !== null} onClick={() => setSaving(true)}
              title={counts.accepted ? undefined : "Accept at least one agent first"}>Save as blueprint draft</Button>
          </>}
        </>} />
      {failure && <Banner tone="error" className="mb-4">{failure}</Banner>}
      {s.blueprint && (
        <Banner tone="success" className="mb-4">
          Saved as <strong>{s.blueprint.name} v{s.blueprint.version}</strong> (draft).{" "}
          <Link to={`/blueprints?name=${encodeURIComponent(s.blueprint.name)}`}>Open it in Blueprints</Link> to plan it, or refine agents in{" "}
          <Link to="/studio">Agent Studio</Link>.
        </Banner>
      )}

      <div className="flex gap-5 items-start">
        <div className="w-[420px] shrink-0 flex flex-col gap-4">
          <Card className="p-[18px] flex flex-col gap-3">
            <div className="flex justify-between items-center">
              <span className="text-[15px] font-semibold">Mission</span>
              <span className="text-small text-text-secondary">{timeAgo(s.created_at, now)} · {s.created_by}</span>
            </div>
            <div className="border border-border rounded-control px-3.5 py-3 text-[14px] leading-[22px] whitespace-pre-wrap">{s.mission}</div>
          </Card>
          <Card className="p-[18px]">
            <div className="text-[15px] font-semibold mb-3">Constraints</div>
            <ul className="m-0 p-0 list-none flex flex-col gap-1.5">
              {constraintSummary(s.constraints).map((line) => (
                <li key={line} className="flex gap-2 text-[13px]"><Icon name="check" size={14} className="text-primary mt-0.5 shrink-0" />{line}</li>
              ))}
            </ul>
          </Card>
          <ArchitectCard config={config} writer={writer} onChanged={onConfigChanged} />
          <VersionsCard versions={s.versions} now={now} />
        </div>

        <div className="flex-1 min-w-0 flex flex-col gap-4">
          {s.pending && (
            <Banner tone="info">
              <span className="flex items-center gap-2">
                <Spinner />
                The architect is working on proposal v{s.pending.version}
                {s.pending.job_status === "queued" ? " (waiting for the agent to pick it up)" : ` on ${s.architect.profile}`}. This page updates by itself.
              </span>
            </Banner>
          )}
          {last?.status === "failed" && <FailedCard v={last} />}
          {latest && proposal && (
            <Card className="overflow-hidden">
              <div className="px-[18px] py-4 flex flex-col gap-1.5">
                <div className="flex justify-between items-center gap-3">
                  <span className="text-[15px] font-semibold">
                    Proposed fleet <span className="text-outline font-normal">·</span> {proposal.agents.length} agent{proposal.agents.length === 1 ? "" : "s"}
                  </span>
                  <Chip tone="info">Proposal v{latest.version} · {counts.accepted} of {counts.total} accepted</Chip>
                </div>
                <p className="m-0 text-[13px] leading-[18px] text-text-secondary">{proposal.summary}</p>
                {proposal.adjustments.length > 0 && (
                  <p className="m-0 text-small text-text-secondary">Fleet Control adjusted: {proposal.adjustments.join("; ")}.</p>
                )}
              </div>
              {proposal.agents.map((a) => {
                const agent = effectiveAgent(a, s.edits[a.id]);
                return (
                  <AgentRow key={a.id} agent={agent} edited={Boolean(s.edits[a.id])} decision={s.decisions[a.id]} writer={writer} busy={busy !== null}
                    onDecide={(d) => void act(`decide:${a.id}`, () => api.decideAgent(sid, a.id, { decision: d }))}
                    onEdit={() => setEditing(agent)} />
                );
              })}
            </Card>
          )}
          {proposal && questions.length > 0 && (
            <QuestionsCard questions={questions} answers={answers} earlier={s.answers} writer={writer} onChange={setAnswers} />
          )}
          {proposal && (
            <div className="grid grid-cols-3 gap-4">
              <KpiTile label="Agents" value={proposal.agents.length} note={fleetShape(proposal.agents)} />
              <KpiTile label="Est. cost / day" value={proposal.estimate.cost_per_day_usd ? `$${proposal.estimate.cost_per_day_usd}` : "—"}
                note={proposal.estimate.basis ?? "The architect gave no estimate"} />
              <KpiTile label="Tests proposed" value={proposal.tests.length} note="Run in Test Lab before any apply" />
            </div>
          )}
        </div>
      </div>

      {editing && (
        <EditAgentModal agent={editing} onClose={() => setEditing(null)}
          onSave={async (edit) => {
            await api.decideAgent(sid, editing.id, { edit });
            setEditing(null);
            await reload();
          }} />
      )}
      {saving && (
        <SaveModal mission={s.mission} accepted={counts.accepted} onClose={() => setSaving(false)}
          onSave={async (name) => {
            await api.saveArchitectBlueprint(sid, name);
            setSaving(false);
            await reload();
            onChanged();
          }} />
      )}
    </>
  );
}

function AgentRow({ agent, edited, decision, writer, busy, onDecide, onEdit }: {
  agent: ProposedAgent; edited: boolean; decision: AgentDecisionValue | undefined; writer: boolean; busy: boolean;
  onDecide: (d: AgentDecisionValue | "proposed") => void; onEdit: () => void;
}) {
  return (
    <div className="flex items-start gap-3.5 px-[18px] py-3.5 border-t border-hairline">
      <span className="size-8 shrink-0 rounded-control bg-secondary-tint text-secondary flex items-center justify-center"><Icon name="bot" /></span>
      <div className={`flex-1 min-w-0 flex flex-col gap-2 ${decision === "removed" ? "opacity-50" : ""}`}>
        <div>
          <div className="font-semibold">
            {agentName(agent)} <Mono className="text-text-secondary font-normal">{agent.id}</Mono>
            {edited && <span className="text-small text-text-secondary font-normal"> · edited</span>}
          </div>
          <div className="text-small text-text-secondary">{agent.role}</div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <span className={`${CHIP} bg-secondary-tint text-secondary font-semibold`}>
            {agent.model.name}{agent.model.data_class === "redacted-only" ? " · redacted input only" : ""}
          </span>
          {agent.skills.map((x) => <span key={`s-${x}`} className={`${CHIP} bg-container-low font-medium`}>{x}</span>)}
          {agent.toolsets.map((x) => <span key={`t-${x}`} className={`${CHIP} bg-container-low font-medium`}>toolset · {x}</span>)}
          {agent.mcps.map((x) => <span key={`m-${x}`} className={`${CHIP} border border-border bg-white text-text-secondary font-medium`}>MCP · {x}</span>)}
          {agent.delegates_to.length > 0 && (
            <span className={`${CHIP} border border-border bg-white text-text-secondary font-medium`}>delegates to {agent.delegates_to.join(", ")}</span>
          )}
        </div>
      </div>
      {writer && (
        <div className="flex gap-1.5 shrink-0 items-center">
          {decision === "accepted" && <Chip tone="success"><span className="flex items-center gap-1"><Icon name="check" size={14} />Accepted</span></Chip>}
          {decision === "removed" && <Chip tone="neutral">Removed</Chip>}
          {!decision && <Button disabled={busy} onClick={() => onDecide("accepted")}>Accept</Button>}
          {decision !== "removed" && <Button disabled={busy} onClick={onEdit}>Edit</Button>}
          {decision
            ? <Button disabled={busy} onClick={() => onDecide("proposed")}>Undo</Button>
            : <Button disabled={busy} onClick={() => onDecide("removed")}>Remove</Button>}
        </div>
      )}
    </div>
  );
}

function QuestionsCard({ questions, answers, earlier, writer, onChange }: {
  questions: string[]; answers: Record<number, string>; earlier: { question: string; answer: string }[]; writer: boolean;
  onChange: (a: Record<number, string>) => void;
}) {
  return (
    <Card className="p-[18px] flex flex-col gap-3.5">
      <div className="flex justify-between items-center gap-3">
        <span className="text-[15px] font-semibold">Open questions from the architect</span>
        <span className="text-small text-text-secondary">Answers feed the next proposal (Ask again)</span>
      </div>
      {questions.map((q, i) => (
        <div key={`${i}-${q}`} className="flex flex-col gap-2">
          <div className="flex gap-2.5 items-start">
            <span className="size-[22px] shrink-0 rounded-full bg-secondary-tint text-secondary text-[12px] font-bold flex items-center justify-center">{i + 1}</span>
            <span className="text-[13px] leading-[18px] pt-0.5">{q}</span>
          </div>
          <div className="pl-8">
            <input className={INPUT} aria-label={`Answer to question ${i + 1}`} disabled={!writer} value={answers[i] ?? ""}
              placeholder={writer ? "Type a reply…" : "Admins and Fleet Architects answer"}
              onChange={(e) => onChange({ ...answers, [i]: e.target.value })} />
          </div>
        </div>
      ))}
      {earlier.length > 0 && (
        <details className="text-small text-text-secondary">
          <summary className="cursor-pointer">Earlier answers ({earlier.length})</summary>
          <ul className="mt-2 mb-0 pl-5 flex flex-col gap-1">
            {earlier.map((a, i) => <li key={i}><span className="font-semibold text-text">{a.question}</span> {a.answer}</li>)}
          </ul>
        </details>
      )}
    </Card>
  );
}

function FailedCard({ v }: { v: ProposalVersion }) {
  return (
    <Card className="p-[18px] flex flex-col gap-2">
      <div className="flex items-center gap-2 font-semibold"><Icon name="warning" className="text-warning" />Proposal v{v.version} could not be used</div>
      <p className="m-0 text-[13px]">{v.error}</p>
      <p className="m-0 text-small text-text-secondary">Ask again to retry; your decisions and answers are kept.</p>
      {v.raw && (
        <details className="text-small">
          <summary className="cursor-pointer text-text-secondary">What the architect answered</summary>
          <pre className="mt-2 mb-0 p-3 bg-container-low rounded-control whitespace-pre-wrap break-words font-mono text-[12px] max-h-[240px] overflow-auto">{v.raw}</pre>
        </details>
      )}
    </Card>
  );
}

function VersionsCard({ versions, now }: { versions: ProposalVersion[]; now: number }) {
  return (
    <Card className="p-[18px]">
      <div className="text-[15px] font-semibold mb-3">Proposals</div>
      <div className="flex flex-col gap-2">
        {[...versions].reverse().map((v) => (
          <div key={v.version} className="flex items-center justify-between gap-3 text-[13px]">
            <span>v{v.version} <span className="text-text-secondary">· asked {timeAgo(v.requested_at, now)}</span></span>
            <Chip tone={v.status === "ready" ? "success" : v.status === "failed" ? "warning" : "info"}>
              {v.status === "ready" ? "Ready" : v.status === "failed" ? "Unusable" : "Working"}
            </Chip>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------- editing and saving

function EditAgentModal({ agent, onClose, onSave }: { agent: ProposedAgent; onClose: () => void; onSave: (edit: Partial<ProposedAgent>) => Promise<void> }) {
  const [role, setRole] = useState(agent.role);
  const [provider, setProvider] = useState(agent.model.provider);
  const [model, setModel] = useState(agent.model.name);
  const [redacted, setRedacted] = useState(agent.model.data_class === "redacted-only");
  const [objective, setObjective] = useState(agent.soul.objective);
  const [boundaries, setBoundaries] = useState(agent.soul.boundaries);
  const [skills, setSkills] = useState(agent.skills);
  const [toolsets, setToolsets] = useState(agent.toolsets);
  const [mcps, setMcps] = useState(agent.mcps);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSave({
        role: role.trim(), skills, toolsets, mcps,
        model: { provider: provider.trim(), name: model.trim(), data_class: redacted ? "redacted-only" : "raw" },
        soul: { ...agent.soul, objective: objective.trim(), boundaries },
      });
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <Modal width={640} title={`Edit ${agentName(agent)}`} subtitle="Your edits go into the blueprint draft; the architect sees them when you ask again." onClose={onClose}
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="agent-edit-form" disabled={busy || !role.trim() || !provider.trim() || !model.trim() || !objective.trim()}>
          {busy && <Spinner />}Save changes
        </Button>
      </>}>
      <form id="agent-edit-form" onSubmit={(e) => void submit(e)}>
        <Field label="Role"><input className={INPUT} value={role} onChange={(e) => setRole(e.target.value)} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Model provider"><input className={INPUT} value={provider} onChange={(e) => setProvider(e.target.value)} /></Field>
          <Field label="Model"><input className={INPUT} value={model} onChange={(e) => setModel(e.target.value)} /></Field>
        </div>
        <label className="flex items-center gap-2 text-small mb-4 cursor-pointer">
          <input type="checkbox" className="accent-primary" checked={redacted} onChange={(e) => setRedacted(e.target.checked)} />
          This model may only see redacted input
        </label>
        <Field label="Objective"><textarea className={`${TEXTAREA} min-h-[64px]`} value={objective} onChange={(e) => setObjective(e.target.value)} /></Field>
        <Field label="Boundaries" hint="What the agent must never do."><TagInput label="Boundaries" values={boundaries} onChange={setBoundaries} placeholder="Add a boundary" /></Field>
        <Field label="Skills"><TagInput label="Skills" values={skills} onChange={setSkills} placeholder="Add a skill" /></Field>
        <Field label="Toolsets"><TagInput label="Toolsets" values={toolsets} onChange={setToolsets} placeholder="Add a toolset" /></Field>
        <Field label="MCP servers"><TagInput label="MCP servers" values={mcps} onChange={setMcps} placeholder="Add an MCP server" /></Field>
        {error && <Banner tone="error">{error}</Banner>}
      </form>
    </Modal>
  );
}

function SaveModal({ mission, accepted, onClose, onSave }: { mission: string; accepted: number; onClose: () => void; onSave: (name: string) => Promise<void> }) {
  const [name, setName] = useState(() => blueprintNameFor(mission));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = BLUEPRINT_NAME.test(name);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSave(name);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <Modal width={520} title="Save as blueprint draft" onClose={onClose}
      subtitle={`The ${accepted} accepted agent${accepted === 1 ? "" : "s"}, their tests, and your constraints as policies. Nothing is applied.`}
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="architect-save-form" disabled={busy || !valid}>{busy && <Spinner />}Save draft</Button>
      </>}>
      <form id="architect-save-form" onSubmit={(e) => void submit(e)}>
        <Field label="Blueprint name" hint={valid ? "Lowercase letters, digits and hyphens." : "Lowercase letters, digits and hyphens, starting with a letter."}>
          <input className={INPUT} autoFocus value={name} onChange={(e) => setName(e.target.value.trim())} aria-invalid={!valid} />
        </Field>
        {error && <Banner tone="error">{error}</Banner>}
      </form>
    </Modal>
  );
}
