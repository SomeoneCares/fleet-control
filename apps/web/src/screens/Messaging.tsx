import { useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { api, type BlueprintDeliveryRule, type Channel, type MessagingDoc } from "../api/client";
import { useAuth } from "../lib/auth";
import { errorText, useLoad, useNow } from "../lib/hooks";
import { CHANNEL_STATUS, DELIVERY_TONE, EVENT_LINK, TEMPLATE_LABEL, channelIdFrom, failuresToday, ruleSummary } from "../lib/messaging";
import { formatDateTime, timeAgo, type Tone } from "../lib/view";
import { Banner, Button, Card, Chip, Field, INPUT, Icon, Modal, Mono, PageHeader, Spinner } from "../components/ui";

// design/screens/Messaging: channels on the instances, the blueprint rules that use them, and what was sent.
export function MessagingScreen() {
  const { can } = useAuth();
  const manage = can("messaging.manage");
  const now = useNow();
  const { data: doc, error, reload } = useLoad(api.messaging, [], 5000);
  const [selected, setSelected] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState(false);
  const [msg, setMsg] = useState<{ tone: Tone; text: string } | null>(null);
  const current = doc?.channels.find((c) => c.id === selected) ?? doc?.channels[0] ?? null;

  async function act(fn: () => Promise<unknown>, done: string) {
    setMsg(null);
    try { await fn(); setMsg({ tone: "info", text: done }); await reload(); } catch (e) { setMsg({ tone: "error", text: errorText(e) }); }
  }

  return (
    <>
      <PageHeader crumb="Estate" title="Messaging"
        subtitle="Hermes messaging gateways on each instance, and the rules that decide which fleet events are delivered where. Every message links back into the portal; a reply in chat is never a decision."
        actions={<>
          {manage && current && <Button icon="message" onClick={() => void act(() => api.testChannel(current.id), `Test message queued for ${current.name}.`)}>Send test message</Button>}
          {manage && <Button variant="primary" icon="plus" onClick={() => setAdding(true)}>Add channel</Button>}
        </>} />
      {msg && <Banner tone={msg.tone} className="mb-4">{msg.text}</Banner>}
      {error && <Banner tone="error" className="mb-4">{error}</Banner>}
      {!doc && !error && <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div>}

      {doc && (
        <>
          <Instances doc={doc} manage={manage} act={act} />
          <div className="flex gap-5 items-start">
            <div className="flex-1 min-w-0 flex flex-col gap-5">
              <Channels doc={doc} current={current} onSelect={setSelected} now={now} />
              <Rules doc={doc} onEdit={can("blueprints.write") ? () => setEditing(true) : undefined} />
            </div>
            {current && <Detail key={current.id} channel={current} doc={doc} manage={manage} act={act} onRemoved={() => setSelected(null)} />}
          </div>
        </>
      )}

      {adding && doc && <AddChannel doc={doc} onClose={() => setAdding(false)}
        onAdded={async (c) => { setAdding(false); setSelected(c.id); setMsg({ tone: "info", text: `${c.name} added; its route is being created on ${c.instance_id}.` }); await reload(); }} />}
      {editing && doc && <EditRules doc={doc} onClose={() => setEditing(false)}
        onSaved={async (text) => { setEditing(false); setMsg({ tone: "info", text }); await reload(); }} />}
    </>
  );
}

function Instances({ doc, manage, act }: { doc: MessagingDoc; manage: boolean; act: (fn: () => Promise<unknown>, done: string) => Promise<void> }) {
  const rows = doc.instances.filter((i) => i.can_discover);
  if (!rows.length) return null;
  return (
    <div className="mb-5 flex flex-col gap-2">
      {rows.map((i) => (
        <div key={i.instance_id} className="flex flex-wrap items-center gap-3 text-small">
          <Mono className="font-semibold">{i.instance_id}</Mono>
          {i.discovered_at === null
            ? <span className="text-text-secondary">not discovered yet</span>
            : <>
                <span className="text-text-secondary">
                  {i.platforms.filter((p) => p.configured).map((p) => `${p.name}${p.state ? ` (${p.state})` : ""}`).join(", ") || "no messaging platform configured"}
                </span>
                <Chip tone={i.webhooks_enabled ? "success" : "warning"}>{i.webhooks_enabled ? "Webhooks on" : "Webhooks off"}</Chip>
                <span className="text-text-secondary">· {timeAgo(i.discovered_at)}</span>
              </>}
          <Button onClick={() => void act(() => api.discoverMessaging(i.instance_id), `Discovering messaging on ${i.instance_id}…`)}>Discover</Button>
          {manage && i.discovered_at !== null && !i.webhooks_enabled && (
            <Button onClick={() => {
              if (!window.confirm(`Turn on the webhook platform on ${i.instance_id}? Hermes restarts its gateway to start it: runs in progress there are interrupted.`)) return;
              void act(() => api.enableWebhooks(i.instance_id), `Enabling webhooks on ${i.instance_id}; its gateway restarts.`);
            }}>Enable webhooks</Button>
          )}
        </div>
      ))}
    </div>
  );
}

function Channels({ doc, current, onSelect, now }: { doc: MessagingDoc; current: Channel | null; onSelect: (id: string) => void; now: number }) {
  const instances = new Set(doc.channels.map((c) => c.instance_id));
  return (
    <Card>
      <div className="flex items-baseline justify-between px-4 py-3.5 border-b border-hairline">
        <h2 className="text-section m-0">Channels</h2>
        <span className="text-small text-text-secondary">{doc.channels.length} channel{doc.channels.length === 1 ? "" : "s"} · {instances.size} instance{instances.size === 1 ? "" : "s"}</span>
      </div>
      {doc.channels.length === 0 && <p className="m-0 px-4 py-4 text-small text-text-secondary">No channel yet. Discover an instance's messaging platforms, then add a channel on one of them.</p>}
      {doc.channels.length > 0 && (
        <div className="grid grid-cols-[minmax(0,2fr)_110px_minmax(0,1fr)_minmax(0,1fr)_140px] gap-3 px-4 py-2 text-label uppercase text-text-secondary border-b border-hairline">
          <span>Channel</span><span>Gateway</span><span>Instance</span><span>Audience</span><span>Status</span>
        </div>
      )}
      {doc.channels.map((c) => {
        const st = CHANNEL_STATUS[c.status];
        const failed = failuresToday(c, doc.deliveries, now);
        return (
          <button key={c.id} type="button" onClick={() => onSelect(c.id)}
            className={`w-full text-left grid grid-cols-[minmax(0,2fr)_110px_minmax(0,1fr)_minmax(0,1fr)_140px] gap-3 px-4 py-3 border-b border-hairline last:border-b-0 cursor-pointer items-center ${current?.id === c.id ? "bg-primary-tint" : "bg-transparent hover:bg-container-low"}`}>
            <span className="min-w-0">
              <span className="block text-[13px] font-medium truncate">{c.name}</span>
              <Mono className="text-[11px] text-text-secondary">{c.ref}</Mono>
            </span>
            <span className="text-[13px] capitalize">{c.platform}</span>
            <Mono className="text-[12px] truncate">{c.instance_id}</Mono>
            <span className="text-[13px] truncate">{c.audience || <span className="text-outline">—</span>}</span>
            <span className="flex flex-col items-start gap-0.5">
              <Chip tone={st.tone}>{st.label}</Chip>
              {failed > 0 && <span className="text-small text-error">{failed} failure{failed === 1 ? "" : "s"} today</span>}
            </span>
          </button>
        );
      })}
    </Card>
  );
}

function Rules({ doc, onEdit }: { doc: MessagingDoc; onEdit?: () => void }) {
  const sum = ruleSummary(doc.rules);
  return (
    <Card>
      <div className="flex items-center justify-between gap-3 px-4 py-3.5 border-b border-hairline">
        <div>
          <h2 className="text-section m-0">Delivery rules</h2>
          <span className="text-small text-text-secondary">Rules are part of the blueprint: these are the applied versions' · {sum.enabled} of {sum.total} enabled</span>
        </div>
        {onEdit && <Button icon="layers" onClick={onEdit}>Edit on a draft</Button>}
      </div>
      {doc.rules.length === 0 && <p className="m-0 px-4 py-4 text-small text-text-secondary">No applied blueprint has delivery rules. Add them on a draft, then plan and apply it.</p>}
      {doc.rules.length > 0 && (
        <div className="grid grid-cols-[minmax(0,1.3fr)_minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-3 px-4 py-2 text-label uppercase text-text-secondary border-b border-hairline">
          <span>When</span><span>Send to</span><span>Message</span><span>Link</span><span>Blueprint</span>
        </div>
      )}
      {doc.rules.map((r) => (
        <div key={`${r.blueprint}:${r.n}`} className={`grid grid-cols-[minmax(0,1.3fr)_minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-3 px-4 py-3 border-b border-hairline last:border-b-0 text-[13px] items-center ${r.enabled ? "" : "opacity-60"}`}>
          <span>{r.event}{!r.enabled && <span className="text-text-secondary"> (off)</span>}</span>
          <span className="min-w-0">
            <Mono className="text-[12px] break-all">{r.to}</Mono>
            {!r.channel && <span className="block text-small text-warning">no such channel: nothing is sent</span>}
          </span>
          <span>{TEMPLATE_LABEL[r.template] ?? r.template}</span>
          <span className="text-text-secondary">{EVENT_LINK[r.when] ?? "—"}</span>
          <Link to="/blueprints" className="truncate">{r.blueprint} v{r.version}</Link>
        </div>
      ))}
    </Card>
  );
}

function Detail({ channel: c, doc, manage, act, onRemoved }: {
  channel: Channel; doc: MessagingDoc; manage: boolean; act: (fn: () => Promise<unknown>, done: string) => Promise<void>; onRemoved: () => void;
}) {
  const st = CHANNEL_STATUS[c.status];
  const sent = doc.deliveries.filter((d) => d.channel === c.id).slice(0, 8);
  const rules = doc.rules.filter((r) => r.channel === c.id);
  return (
    <Card className="w-[380px] shrink-0 p-[18px] flex flex-col gap-3.5">
      <div className="flex justify-between items-start gap-3">
        <div className="min-w-0">
          <div className="text-[15px] font-semibold truncate">{c.name}</div>
          <div className="text-small text-text-secondary">Channel · added {formatDateTime(c.created_at)}</div>
        </div>
        <Chip tone={st.tone}>{st.label}</Chip>
      </div>
      {c.detail && <Banner tone={st.tone === "error" ? "error" : "warning"} className="text-small">{c.detail}</Banner>}
      {c.route_job?.error && <Banner tone="error" className="text-small">Route: {c.route_job.error}</Banner>}

      <div className="border border-hairline rounded-control divide-y divide-hairline text-[13px]">
        <Row label="Blueprints say"><Mono>{c.ref}</Mono></Row>
        <Row label="Gateway">Hermes {c.platform} on <Mono>{c.instance_id}</Mono></Row>
        <Row label="Delivers to">{c.chat_id ? <>chat <Mono>{c.chat_id}</Mono></> : "the platform's home channel"}</Row>
        <Row label="Route"><Mono>{c.route}</Mono> <span className="text-text-secondary">(deliver only; its secret stays on the instance)</span></Row>
        <Row label="Titles">{c.show_titles ? "Room questions and output names are shown" : "Hidden: messages carry the event, case id and a link"}</Row>
        <Row label="Rules">{rules.length ? rules.map((r) => r.event).join(", ") : "none use it yet"}</Row>
      </div>
      <p className="m-0 text-small text-text-secondary">
        Replies in this chat are not decisions. Decisions are recorded only in the portal, so the audit trail stays complete.
      </p>

      {manage && (
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => void act(() => api.changeChannel(c.id, { show_titles: !c.show_titles }), c.show_titles ? "Titles hidden." : "Titles shown.")}>
            {c.show_titles ? "Hide titles" : "Show titles"}
          </Button>
          <Button onClick={() => void act(() => api.changeChannel(c.id, { enabled: !c.enabled }), c.enabled ? `${c.name} switched off.` : `${c.name} switched on.`)}>
            {c.enabled ? "Disable" : "Enable"}
          </Button>
          <Button onClick={() => void act(() => api.recreateRoute(c.id), `Recreating ${c.route} on ${c.instance_id}…`)}>Recreate route</Button>
          <Button variant="danger" onClick={() => {
            if (!window.confirm(`Remove ${c.name}? Its route is removed from ${c.instance_id}; rules naming ${c.ref} stop delivering. What was sent stays in the record.`)) return;
            void act(async () => { await api.removeChannel(c.id); onRemoved(); }, `${c.name} removed.`);
          }}>Remove</Button>
        </div>
      )}

      <div className="flex flex-col gap-2 border-t border-hairline pt-3.5">
        <div className="text-label uppercase text-text-secondary">Sent</div>
        {sent.length === 0 && <p className="m-0 text-small text-text-secondary">Nothing yet.</p>}
        {sent.map((d) => (
          <details key={d.id} className="text-small">
            <summary className="cursor-pointer flex items-center gap-2">
              <Chip tone={DELIVERY_TONE[d.status]}>{d.status}</Chip>
              <span className="truncate">{d.event === "test" ? `Test by ${d.by}` : doc.events[d.event] ?? d.event}</span>
              <span className="text-text-secondary ml-auto shrink-0">{timeAgo(d.at)}</span>
            </summary>
            <pre className="mt-1.5 mb-0 p-2.5 bg-container-low rounded-control whitespace-pre-wrap break-words font-mono text-[11px]">{d.text}</pre>
            {d.error && <div className="text-error mt-1">{d.error}</div>}
          </details>
        ))}
      </div>
    </Card>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[104px_minmax(0,1fr)] gap-2.5 px-3 py-2.5">
      <span className="text-text-secondary">{label}</span><span className="min-w-0 break-words">{children}</span>
    </div>
  );
}

function AddChannel({ doc, onClose, onAdded }: { doc: MessagingDoc; onClose: () => void; onAdded: (c: Channel) => Promise<void> }) {
  const discovered = doc.instances.filter((i) => i.discovered_at !== null && i.platforms.some((p) => p.configured));
  const [instance, setInstance] = useState(discovered[0]?.instance_id ?? "");
  const platforms = (discovered.find((i) => i.instance_id === instance)?.platforms ?? []).filter((p) => p.configured);
  const [platform, setPlatform] = useState("");
  const chosen = platforms.some((p) => p.id === platform) ? platform : platforms[0]?.id ?? "";
  const home = platforms.find((p) => p.id === chosen)?.home_channel;
  const [name, setName] = useState("");
  const [chatId, setChatId] = useState("");
  const [audience, setAudience] = useState("");
  const [titles, setTitles] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = channelIdFrom(name);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await onAdded(await api.createChannel({ name: name.trim(), instance_id: instance, platform: chosen, chat_id: chatId.trim() || undefined,
                                              audience: audience.trim(), show_titles: titles }));
    } catch (err) { setError(errorText(err)); setBusy(false); }
  }
  return (
    <Modal width={560} title="Add channel" onClose={onClose}
      subtitle="A messaging platform an instance's Hermes gateway has connected. Fleet Control's agent adds a deliver-only route for it; the platform's credentials never leave the instance."
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="add-channel" disabled={busy || !instance || !chosen || id.length < 2}>{busy && <Spinner />}Add channel</Button>
      </>}>
      {discovered.length === 0
        ? <Banner tone="warning">No instance has a configured messaging platform yet. Discover one on this screen first; platforms are set up on the instance (Hermes: <Mono>hermes gateway setup</Mono>).</Banner>
        : (
          <form id="add-channel" onSubmit={(e) => void submit(e)}>
            <Field label="Instance">
              <select className={INPUT} value={instance} onChange={(e) => setInstance(e.target.value)}>
                {discovered.map((i) => <option key={i.instance_id} value={i.instance_id}>{i.instance_id} ({i.environment})</option>)}
              </select>
            </Field>
            <Field label="Platform">
              <select className={INPUT} value={chosen} onChange={(e) => setPlatform(e.target.value)}>
                {platforms.map((p) => <option key={p.id} value={p.id}>{p.name}{p.state ? ` (${p.state})` : ""}</option>)}
              </select>
            </Field>
            <Field label="Name" hint={id ? <>Blueprint rules send to <Mono>{chosen}:{id}</Mono></> : "e.g. Ops on-call"}>
              <input className={INPUT} autoFocus value={name} maxLength={60} onChange={(e) => setName(e.target.value)} placeholder="Ops on-call" />
            </Field>
            <Field label="Chat" hint={home ? `Optional. Empty = the home channel${home.name ? ` (${home.name})` : ""}.` : "Optional. Empty = the platform's home channel."}>
              <input className={INPUT} value={chatId} maxLength={120} onChange={(e) => setChatId(e.target.value)} placeholder="-1001234567890" />
            </Field>
            <Field label="Audience" hint="Who reads it, in words. Shown here only.">
              <input className={INPUT} value={audience} maxLength={120} onChange={(e) => setAudience(e.target.value)} placeholder="Fleet operators" />
            </Field>
            <label className="flex items-start gap-2 text-[13px] cursor-pointer">
              <input type="checkbox" className="mt-0.5" checked={titles} onChange={(e) => setTitles(e.target.checked)} />
              <span>Show titles: room questions and output names travel in the message. Leave off unless everyone in the chat may read them.</span>
            </label>
            {error && <Banner tone="error" className="mt-3">{error}</Banner>}
          </form>
        )}
    </Modal>
  );
}

function EditRules({ doc, onClose, onSaved }: { doc: MessagingDoc; onClose: () => void; onSaved: (text: string) => Promise<void> }) {
  const [draft, setDraft] = useState(doc.drafts[0] ? `${doc.drafts[0].name}@${doc.drafts[0].version}` : "");
  const [name, version] = draft.split("@");
  const { data: bp } = useLoad(() => (name ? api.blueprint(name, Number(version)) : Promise.resolve(null)), [draft]);
  const [rules, setRules] = useState<BlueprintDeliveryRule[] | null>(null);
  const shown = rules ?? ((bp?.parsed as { delivery?: BlueprintDeliveryRule[] } | undefined)?.delivery ?? []).map((r) => ({ ...r, enabled: r.enabled ?? true }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refs = doc.channels.map((c) => c.ref);
  const set = (i: number, patch: Partial<BlueprintDeliveryRule>) => setRules(shown.map((r, n) => (n === i ? { ...r, ...patch } : r)));

  async function save() {
    setBusy(true); setError(null);
    try { await api.saveDeliveryRules(name, Number(version), shown); await onSaved(`Saved ${shown.length} rules on ${name} v${version}. Plan and apply it for them to deliver.`); }
    catch (err) { setError(errorText(err)); setBusy(false); }
  }
  return (
    <Modal width={760} title="Delivery rules" onClose={onClose}
      subtitle="Rules live in the blueprint and reach the fleet by plan and apply, so they are edited on a draft."
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={busy || !draft} onClick={() => void save()}>{busy && <Spinner />}Save to draft</Button>
      </>}>
      {doc.drafts.length === 0
        ? <Banner tone="warning">No draft to edit. Create a new version of a blueprint in <Link to="/blueprints">Blueprints</Link> first.</Banner>
        : <>
            <Field label="Draft">
              <select className={INPUT} value={draft} onChange={(e) => { setDraft(e.target.value); setRules(null); }}>
                {doc.drafts.map((d) => <option key={`${d.name}@${d.version}`} value={`${d.name}@${d.version}`}>{d.name} v{d.version}</option>)}
              </select>
            </Field>
            <div className="flex flex-col gap-2">
              {shown.map((r, i) => (
                <div key={i} className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,1.2fr)_minmax(0,1fr)_auto_auto] gap-2 items-center">
                  <select className={INPUT} aria-label="When" value={r.when} onChange={(e) => set(i, { when: e.target.value })}>
                    {Object.entries(doc.events).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                  </select>
                  <input className={INPUT} aria-label="Send to" list="channel-refs" value={r.to} onChange={(e) => set(i, { to: e.target.value })} />
                  <select className={INPUT} aria-label="Message" value={r.template} onChange={(e) => set(i, { template: e.target.value })}>
                    {doc.templates.map((t) => <option key={t} value={t}>{TEMPLATE_LABEL[t] ?? t}</option>)}
                  </select>
                  <label className="flex items-center gap-1 text-small"><input type="checkbox" checked={r.enabled} onChange={(e) => set(i, { enabled: e.target.checked })} />on</label>
                  <button type="button" aria-label="Remove rule" className="cursor-pointer bg-transparent text-text-secondary" onClick={() => setRules(shown.filter((_, n) => n !== i))}><Icon name="close" /></button>
                </div>
              ))}
              <datalist id="channel-refs">{refs.map((r) => <option key={r} value={r} />)}</datalist>
              <div><Button icon="plus" onClick={() => setRules([...shown, { when: "decision_room.opened", to: refs[0] ?? "", template: "decision-request", enabled: true }])}>Add rule</Button></div>
            </div>
            {error && <Banner tone="error" className="mt-3">{error}</Banner>}
          </>}
    </Modal>
  );
}
