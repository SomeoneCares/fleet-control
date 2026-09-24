import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { api, type AskConfigDoc, type AskThread, type AskTurn } from "../api/client";
import { answerParts, outputName, SOURCE_KIND_LABEL, sourceLink, splitSources } from "../lib/ask";
import { CLASSIFICATION_TONE } from "../lib/content";
import { errorText, useLoad } from "../lib/hooks";
import { VERDICT_TONE } from "../lib/testlab";
import { timeAgo } from "../lib/view";
import { Banner, Button, Card, Chip, Field, INPUT, Icon, Modal, Mono, PageHeader, Spinner, TEXTAREA } from "../components/ui";
import type { Classification } from "../api/client";

// design/screens/AskFleet: questions go to one orchestrator, answered only from what the person may see.
export function AskFleetScreen() {
  const { data: cfgDoc, error: cfgError, reload: reloadCfg } = useLoad(api.askConfig, []);
  const { data: threads, reload: reloadThreads } = useLoad(api.askThreads, [], 10_000);
  const [search, setSearch] = useSearchParams();
  const current = search.get("c");
  const cfg = cfgDoc?.config ?? null;
  const [configuring, setConfiguring] = useState(false);

  return (
    <>
      <PageHeader crumb="Workspace" title="Ask the fleet"
        subtitle={cfg
          ? <>Questions go to <Mono>{cfg.profile}</Mono> on {cfg.instance_id}. It answers only from content you are allowed to see, and shows what it used.</>
          : "Questions go to the fleet's orchestrator. It answers only from content you are allowed to see, and shows what it used."}
        actions={<>
          {cfgDoc && cfgDoc.candidates.length > 0 && <Button icon="gear" onClick={() => setConfiguring(true)}>Orchestrator</Button>}
          <Button icon="plus" disabled={!current} onClick={() => setSearch({})}>New conversation</Button>
        </>} />
      {cfgError && <Banner tone="error" className="mb-4">{cfgError}</Banner>}
      {cfgDoc && !cfg && (
        <Banner tone="warning" className="mb-4">
          {cfgDoc.candidates.length
            ? <>No orchestrator is chosen yet. <button type="button" className="underline cursor-pointer bg-transparent" onClick={() => setConfiguring(true)}>Choose one</button>: a profile whose blueprint grants it the content zones it should answer from.</>
            : "No orchestrator is chosen yet. An Admin chooses it here."}
        </Banner>
      )}
      {cfg && <Scope cfg={cfg} />}

      <div className="flex gap-5 items-start">
        <Card className="w-[260px] shrink-0 p-2 flex flex-col">
          <div className="px-2.5 pt-2 pb-1.5 text-label uppercase text-text-secondary">Your conversations</div>
          {(threads ?? []).length === 0 && <p className="m-0 px-2.5 pb-2.5 text-small text-text-secondary">None yet. They are yours alone.</p>}
          {(threads ?? []).map((t) => (
            <button key={t.id} type="button" onClick={() => setSearch({ c: t.id })}
              className={`text-left px-2.5 py-2 rounded-control cursor-pointer ${t.id === current ? "bg-primary-tint" : "hover:bg-container-low bg-transparent"}`}>
              <div className="text-[13px] font-medium leading-[18px] line-clamp-2">{t.title}</div>
              <div className="text-small text-text-secondary flex items-center gap-1.5">
                {t.status === "running" && <Spinner />}{timeAgo(t.updated_at)} · {t.turns} question{t.turns === 1 ? "" : "s"}
              </div>
            </button>
          ))}
        </Card>
        <div className="flex-1 min-w-0">
          {current
            ? <Conversation key={current} id={current} onChanged={reloadThreads} />
            : <Composer disabled={!cfg} onAsk={async (q) => { const t = await api.ask(q); await reloadThreads(); setSearch({ c: t.id }); }} />}
        </div>
      </div>

      {configuring && cfgDoc && (
        <ConfigModal doc={cfgDoc} onClose={() => setConfiguring(false)} onSaved={async () => { setConfiguring(false); await reloadCfg(); }} />
      )}
    </>
  );
}

function Scope({ cfg }: { cfg: NonNullable<AskConfigDoc["config"]> }) {
  return (
    <div className="mb-4 text-small text-text-secondary flex flex-wrap gap-x-4 gap-y-1">
      <span>Searches: {cfg.searched_zones.length ? cfg.searched_zones.map((z) => <Mono key={z} className="mr-1.5">{z}</Mono>) : "no zone (nothing you may read is granted to the orchestrator)"}</span>
      {cfg.not_searched.length > 0 && (
        <span title="You may read these, but no applied blueprint grants them to the orchestrator, so it never sees them.">
          Not searched: {cfg.not_searched.map((z) => <Mono key={z} className="mr-1.5">{z}</Mono>)}
        </span>
      )}
      {cfg.instance_status !== "healthy" && <span className="text-warning">{cfg.instance_id} is {cfg.instance_status}</span>}
    </div>
  );
}

function Conversation({ id, onChanged }: { id: string; onChanged: () => Promise<void> }) {
  const { data: thread, error, reload } = useLoad(() => api.askThread(id), [id], 3000);
  const running = thread?.turns.some((t) => t.status === "running") ?? false;
  const end = useRef<HTMLDivElement>(null);
  const turns = thread?.turns.length ?? 0;
  const answered = thread?.turns.filter((t) => t.status !== "running").length ?? 0;
  useEffect(() => { end.current?.scrollIntoView?.({ block: "end", behavior: "smooth" }); }, [turns, answered]);

  if (error) return <Banner tone="error">{error}</Banner>;
  if (!thread) return <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div>;
  return (
    <div className="flex flex-col gap-4">
      {thread.turns.map((t) => <Turn key={t.n} thread={thread} turn={t} onChanged={async () => { await reload(); await onChanged(); }} />)}
      <div ref={end} />
      <Composer follow disabled={running} onAsk={async (q) => { await api.askFollowUp(id, q); await reload(); await onChanged(); }} />
    </div>
  );
}

function Turn({ thread, turn: t, onChanged }: { thread: AskThread; turn: AskTurn; onChanged: () => Promise<void> }) {
  const [saving, setSaving] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  const { used, unused } = splitSources(t);
  return (
    <>
      <div className="self-end max-w-[75%] bg-primary-tint text-text rounded-card px-4 py-2.5 leading-[22px] whitespace-pre-wrap break-words">{t.question}</div>
      <Card className="p-[18px] flex flex-col gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Icon name="spark" className="text-secondary" />
          <span className="text-[13px] font-semibold text-secondary">{t.orchestrator.profile}</span>
          <span className="text-small text-text-secondary">
            · {t.status === "answered" ? `used ${used.length} of ${t.sources.length} source${t.sources.length === 1 ? "" : "s"}` : `${t.sources.length} source${t.sources.length === 1 ? "" : "s"} sent`}
          </span>
          {t.grounding && <Chip tone={VERDICT_TONE[t.grounding.verdict]} className="ml-auto">{t.grounding.verdict}</Chip>}
        </div>

        {t.status === "running" && (
          <div className="flex items-center gap-3 text-text-secondary">
            <Spinner /> Answering on {t.orchestrator.instance_id}…
            <Button className="ml-auto" onClick={async () => {
              try { await api.stopAsk(thread.id, t.n); await onChanged(); } catch (e) { setStopError(errorText(e)); }
            }}>Stop</Button>
          </div>
        )}
        {stopError && <Banner tone="error">{stopError}</Banner>}
        {t.status === "failed" && <Banner tone="error">No answer: {t.error}</Banner>}

        {t.answer && (
          <div className="leading-[22px] whitespace-pre-wrap break-words">
            {answerParts(t.answer).map((p, i) => "cite" in p
              ? <a key={i} href={`#${thread.id}-${t.n}-${p.cite}`} className={`text-[11px] font-semibold align-super mx-0.5 no-underline ${t.cited.includes(p.cite) ? "text-primary" : "text-error line-through"}`}>{p.cite}</a>
              : <span key={i}>{p.text}</span>)}
          </div>
        )}
        {t.grounding && <div className="text-small text-text-secondary">{t.grounding.detail}</div>}
        {t.dropped.length > 0 && <Banner tone="warning">It cited {t.dropped.join(", ")}, which it was never given; those citations were dropped.</Banner>}
        {t.format === "text" && <div className="text-small text-text-secondary">The orchestrator answered in plain text rather than the format asked for; citations were read from the text.</div>}

        {t.status === "answered" && (
          <div className="border-t border-hairline pt-3 flex flex-col gap-2">
            <div className="text-label uppercase text-text-secondary">Sources</div>
            {used.length === 0 && <p className="m-0 text-small text-text-secondary">None: the answer rests on nothing it was given.</p>}
            {used.map((s) => <SourceRow key={s.id} anchor={`${thread.id}-${t.n}-${s.id}`} s={s} />)}
            {unused.length > 0 && (
              <details className="text-small">
                <summary className="cursor-pointer text-text-secondary">Also sent, not used ({unused.length})</summary>
                <div className="mt-2 flex flex-col gap-2 opacity-75">{unused.map((s) => <SourceRow key={s.id} anchor={`${thread.id}-${t.n}-${s.id}`} s={s} />)}</div>
              </details>
            )}
            {t.tool_calls && t.tool_calls.length > 0 && (
              <div className="text-small text-warning">It also called: {t.tool_calls.map((c) => c.name).join(", ")}</div>
            )}
            <div className="flex items-center gap-3 pt-1">
              {t.saved_output
                ? <Link to={`/outputs?id=${encodeURIComponent(t.saved_output)}`} className="text-small">Saved to Fleet outputs</Link>
                : <Button icon="download" onClick={() => setSaving(true)}>Save to Fleet outputs</Button>}
            </div>
          </div>
        )}
      </Card>
      {saving && <SaveModal thread={thread} turn={t} onClose={() => setSaving(false)} onSaved={async () => { setSaving(false); await onChanged(); }} />}
    </>
  );
}

function SourceRow({ s, anchor }: { s: AskTurn["sources"][number]; anchor: string }) {
  return (
    <div id={anchor} className="flex items-start gap-2.5 text-[13px]">
      <Mono className="text-[11px] font-semibold text-primary mt-0.5 w-6 shrink-0">{s.id}</Mono>
      <div className="min-w-0 flex-1">
        <Link to={sourceLink(s)} className="font-medium break-words">{s.label}</Link>
        <div className="text-small text-text-secondary">
          {SOURCE_KIND_LABEL[s.kind]} · <Mono>{s.zone}</Mono>{s.truncated && " · excerpt"}
        </div>
      </div>
      {s.classification && <Chip tone={CLASSIFICATION_TONE[s.classification as Classification]}>{s.classification}</Chip>}
    </div>
  );
}

function Composer({ onAsk, disabled, follow = false }: { onAsk: (q: string) => Promise<void>; disabled: boolean; follow?: boolean }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(e?: FormEvent) {
    e?.preventDefault();
    if (!q.trim() || busy || disabled) return;
    setBusy(true); setError(null);
    try { await onAsk(q.trim()); setQ(""); } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  }
  return (
    <Card className="p-3">
      <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-2">
        <textarea className={TEXTAREA} rows={follow ? 2 : 3} value={q} maxLength={1000} disabled={disabled && !follow}
          placeholder={follow ? "Ask a follow-up…" : "Ask the fleet a question, e.g. which open cases touch Cyprus?"}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(); } }} />
        <div className="flex items-center gap-3">
          <span className="text-small text-text-secondary">
            {follow && disabled ? "Waiting for the answer…" : "Answers are logged in the audit trail."}
          </span>
          <Button variant="primary" type="submit" className="ml-auto" disabled={busy || disabled || !q.trim()}>{busy && <Spinner />}Ask</Button>
        </div>
        {error && <Banner tone="error">{error}</Banner>}
      </form>
    </Card>
  );
}

function SaveModal({ thread, turn, onClose, onSaved }: { thread: AskThread; turn: AskTurn; onClose: () => void; onSaved: () => Promise<void> }) {
  const { data: targets, error: loadError } = useLoad(() => api.askSaveTargets(thread.id, turn.n), [thread.id, turn.n]);
  const [zone, setZone] = useState("");
  const [name, setName] = useState(outputName(turn.question));
  const [caseId, setCaseId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pick = zone || targets?.zones[0] || "";
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await api.saveAskAnswer(thread.id, turn.n, { zone: pick, name: name.trim(), case: caseId.trim() || undefined }); await onSaved(); }
    catch (err) { setError(errorText(err)); setBusy(false); }
  }
  return (
    <Modal width={520} title="Save to Fleet outputs" onClose={onClose}
      subtitle="The answer, its question and its sources are kept as an output, attributed to the orchestrator and to you."
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="ask-save" disabled={busy || !pick || !name.trim()}>{busy && <Spinner />}Save</Button>
      </>}>
      {loadError && <Banner tone="error">{loadError}</Banner>}
      <form id="ask-save" onSubmit={(e) => void submit(e)}>
        <Field label="Zone" hint="Only zones whose readers could already read every source it cites, so saving shows nobody anything new.">
          {targets && targets.zones.length === 0
            ? <Banner tone="warning">No zone you may read is narrow enough for these sources.</Banner>
            : <select className={INPUT} value={pick} onChange={(e) => setZone(e.target.value)}>
                {(targets?.zones ?? []).map((z) => <option key={z} value={z}>{z}</option>)}
              </select>}
        </Field>
        <Field label="Name"><input className={INPUT} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Case" hint="Optional."><input className={INPUT} value={caseId} maxLength={64} onChange={(e) => setCaseId(e.target.value)} placeholder="AML-2026-0412" /></Field>
        {targets && <p className="m-0 text-small text-text-secondary">Classification: <b>{targets.classification}</b>, the strictest of its sources.</p>}
        {error && <Banner tone="error" className="mt-3">{error}</Banner>}
      </form>
    </Modal>
  );
}

function ConfigModal({ doc, onClose, onSaved }: { doc: AskConfigDoc; onClose: () => void; onSaved: () => Promise<void> }) {
  const [instance, setInstance] = useState(doc.config?.instance_id ?? doc.candidates[0]?.instance_id ?? "");
  const profiles = doc.candidates.find((c) => c.instance_id === instance)?.profiles ?? [];
  const [profile, setProfile] = useState(doc.config?.profile ?? "");
  const chosen = profiles.includes(profile) ? profile : profiles[0] ?? "";
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await api.setAskConfig({ instance_id: instance, profile: chosen }); await onSaved(); }
    catch (err) { setError(errorText(err)); setBusy(false); }
  }
  return (
    <Modal width={520} title="Orchestrator" onClose={onClose}
      subtitle="The profile every question goes to. It only ever sees the zones its applied blueprint grants it (content_zones), and of those, only what the person asking may read."
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="ask-config" disabled={busy || !instance || !chosen}>{busy && <Spinner />}Save</Button>
      </>}>
      <form id="ask-config" onSubmit={(e) => void submit(e)}>
        <Field label="Instance">
          <select className={INPUT} value={instance} onChange={(e) => setInstance(e.target.value)}>
            {doc.candidates.map((c) => <option key={c.instance_id} value={c.instance_id}>{c.instance_id} ({c.environment})</option>)}
          </select>
        </Field>
        <Field label="Profile" hint="A profile with no tools is best: an answer that called tools cannot be shown to rest only on its sources.">
          <select className={INPUT} value={chosen} onChange={(e) => setProfile(e.target.value)}>
            {profiles.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </Field>
        {error && <Banner tone="error">{error}</Banner>}
      </form>
      <AssetSection instance={instance} />
    </Modal>
  );
}

// No profile fits? Fleet Control can write one: a tool-less fc-orchestrator granted the zones chosen here, as a
// blueprint draft to plan and apply like any other (the same way the Fleet Architect's profile is made).
function AssetSection({ instance }: { instance: string }) {
  const { data: zones } = useLoad(api.contentZones, []);
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function create() {
    setBusy(true); setError(null);
    try { const r = await api.createAskAsset({ instance_id: instance, zones: picked }); setDone(`${r.name} v${r.version}`); }
    catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  }
  return (
    <div className="border-t border-hairline pt-4 mt-2 flex flex-col gap-2.5">
      <div className="text-[13px] font-semibold">No suitable profile?</div>
      <p className="m-0 text-small text-text-secondary">
        Create the <Mono>fleet-control-orchestrator</Mono> blueprint: one <Mono>fc-orchestrator</Mono> profile with no tools, granted the
        zones below. Plan and apply it to {instance || "the instance"}, then choose <Mono>fc-orchestrator</Mono> here.
      </p>
      <div className="flex flex-wrap gap-x-4 gap-y-1.5">
        {(zones ?? []).map((z) => (
          <label key={z.id} className="flex items-center gap-1.5 text-[13px] cursor-pointer">
            <input type="checkbox" checked={picked.includes(z.id)}
              onChange={(e) => setPicked((p) => (e.target.checked ? [...p, z.id] : p.filter((x) => x !== z.id)))} />
            <Mono>{z.id}</Mono>
          </label>
        ))}
      </div>
      <div className="flex items-center gap-3">
        <Button disabled={busy || !instance || picked.length === 0} onClick={() => void create()}>{busy && <Spinner />}Create blueprint</Button>
        {done && <span className="text-small">Saved {done} as a draft. <Link to="/blueprints">Plan and apply it in Blueprints</Link>.</span>}
      </div>
      {error && <Banner tone="error">{error}</Banner>}
    </div>
  );
}
