import { useState, type FormEvent, type ReactNode } from "react";
import { api, type ApiToken, type SettingsValues } from "../api/client";
import { useAuth } from "../lib/auth";
import { errorText, useLoad, useNow } from "../lib/hooks";
import { lifetimeChoices, tokenState } from "../lib/tokens";
import { ENV_LABEL, formatDate, formatDateTime } from "../lib/view";
import { Banner, Button, Card, Chip, Field, INPUT, Icon, Modal, Mono, PageHeader, Spinner, type IconName } from "../components/ui";

type TabKey = "general" | "observability" | "approvals" | "tokens" | "notifications";

// design/screens/Settings: General · Observability · Approvals · API tokens · Notifications
const TABS: { key: TabKey; label: string; icon: IconName; permission?: string; slice?: number }[] = [
  { key: "general", label: "General", icon: "gear", permission: "settings.read" },
  { key: "observability", label: "Observability", icon: "graph", permission: "settings.read" },
  { key: "approvals", label: "Approvals", icon: "check", permission: "settings.read" },
  { key: "tokens", label: "API tokens", icon: "lock" },
  { key: "notifications", label: "Notifications", icon: "message" },
];

export function SettingsScreen() {
  const { can } = useAuth();
  const tabs = TABS.filter((t) => !t.permission || can(t.permission));
  const [tab, setTab] = useState<TabKey>(tabs[0].key);

  return (
    <>
      <PageHeader crumb="Workspace" title="Settings" />
      <div className="flex gap-5 items-start">
        <div role="tablist" aria-label="Settings sections" aria-orientation="vertical" className="w-[220px] shrink-0 flex flex-col gap-0.5">
          {tabs.map((t) => (
            <button key={t.key} type="button" role="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)}
              className={`h-[34px] flex items-center gap-2.5 px-2.5 rounded-control text-[13px] text-left cursor-pointer ${tab === t.key ? "bg-primary-tint text-primary font-semibold" : "text-text font-medium hover:bg-container-low"}`}>
              <Icon name={t.icon} />
              {t.label}
              {t.slice && <span className="ml-auto text-[11px] font-normal text-text-secondary">Slice {t.slice}</span>}
            </button>
          ))}
        </div>
        <Card className="flex-1 max-w-[720px] min-w-0 overflow-hidden">
          {tab === "general" && <GeneralPanel />}
          {tab === "approvals" && <ApprovalsPanel />}
          {tab === "tokens" && <TokensPanel />}
          {tab === "observability" && <ObservabilityPanel />}
          {tab === "notifications" && <NotificationsPanel />}
        </Card>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- notifications (every person, for themselves)

const ADDRESS_HINT: Record<string, string> = {
  telegram: "Your numeric Telegram user id (ask @userinfobot). Start a chat with the Hermes bot once, or it cannot write to you.",
  email: "Your email address.",
};

function NotificationsPanel() {
  const { data, error, reload } = useLoad(api.myNotifications, []);
  const [address, setAddress] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "info" | "error" | "success"; text: string } | null>(null);
  if (!data) return <Loading error={error} />;
  const via = data.prefs.via;
  const shown = address ?? (via ? data.prefs.addresses[via] ?? "" : "");
  const platform = data.platforms.find((p) => p.platform === via);

  async function change(body: Parameters<typeof api.changeMyNotifications>[0], done?: string) {
    setBusy("save"); setMsg(null);
    try { await api.changeMyNotifications(body); await reload(); setAddress(null); if (done) setMsg({ tone: "success", text: done }); }
    catch (e) { setMsg({ tone: "error", text: errorText(e) }); } finally { setBusy(null); }
  }

  return (
    <>
      <PanelHead title="Notifications" subtitle="What reaches you personally, and where. Your address is yours: nobody else sees or sets it." />
      {!data.messaging_enabled && <Banner tone="warning" className="m-5">Messaging is switched off for this workspace, so nothing is sent (Settings → General).</Banner>}
      {data.messaging_enabled && data.platforms.length === 0 && (
        <Banner tone="info" className="m-5">No way to reach people directly yet: an Admin adds a direct-message channel in Messaging (Add channel → Direct messages).</Banner>
      )}
      {data.platforms.length > 0 && (
        <>
          <Row label="Reach me on" hint={platform && platform.status !== "ready" ? `Not delivering right now: ${platform.detail ?? platform.status}` : "The platforms an Admin has set up for direct messages."}>
            <select className={INPUT} aria-label="Reach me on" value={via ?? ""} disabled={busy !== null}
              onChange={(e) => void change({ via: e.target.value })}>
              <option value="">Nowhere (no notifications)</option>
              {data.platforms.map((p) => <option key={p.platform} value={p.platform}>{p.platform}</option>)}
            </select>
          </Row>
          {via && (
            <Row label="My address" hint={ADDRESS_HINT[via] ?? `Your address on ${via}.`}>
              <div className="flex gap-2">
                <input className={INPUT} aria-label="My address" value={shown} maxLength={120} onChange={(e) => setAddress(e.target.value)} />
                <Button disabled={busy !== null || address === null} onClick={() => void change({ address: shown }, "Address saved.")}>Save</Button>
              </div>
            </Row>
          )}
          <Row label="Tell me when" hint="Only what your role could act on, and only about things you may see.">
            <div className="flex flex-col gap-1.5">
              {data.events.map((e) => (
                <label key={e.event} className="flex items-center gap-2 text-[13px] cursor-pointer">
                  <input type="checkbox" className="accent-primary" checked={e.on} disabled={busy !== null}
                    onChange={(ev) => void change({ events: { [e.event]: ev.target.checked } })} />
                  {e.label}
                </label>
              ))}
              {data.events.length === 0 && <span className="text-small text-text-secondary">Your role has nothing to be told about.</span>}
            </div>
          </Row>
          <Row label="Titles" hint="Off: messages carry what happened, a case id and a link. On: also the room's question.">
            <label className="flex items-center gap-2 text-[13px] cursor-pointer">
              <input type="checkbox" className="accent-primary" checked={data.prefs.show_titles} disabled={busy !== null}
                onChange={(e) => void change({ show_titles: e.target.checked })} />
              Include titles in my messages
            </label>
          </Row>
          <div className="px-5 py-4 flex items-center gap-3">
            <Button disabled={busy !== null || !via || !data.prefs.addresses[via]} onClick={async () => {
              setBusy("test"); setMsg(null);
              try { await api.testMyNotifications(); setMsg({ tone: "info", text: "Test sent. It should reach you in a few seconds." }); }
              catch (e) { setMsg({ tone: "error", text: errorText(e) }); } finally { setBusy(null); }
            }}>{busy === "test" && <Spinner />}Send me a test</Button>
            <span className="text-small text-text-secondary">A reply to a message is never a decision: decide in the portal.</span>
          </div>
        </>
      )}
      {msg && <Banner tone={msg.tone} className="mx-5 mb-5">{msg.text}</Banner>}
    </>
  );
}

// ---------------------------------------------------------------- shared panel parts

function PanelHead({ title, subtitle }: { title: string; subtitle?: ReactNode }) {
  return (
    <div className="px-5 py-4 border-b border-hairline">
      <div className="text-[15px] font-semibold">{title}</div>
      {subtitle && <div className="text-small text-text-secondary mt-0.5">{subtitle}</div>}
    </div>
  );
}

function Row({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="px-5 py-4 border-b border-hairline grid grid-cols-[220px_minmax(0,1fr)] gap-4 items-center">
      <div>
        <div className="text-[13px] font-medium">{label}</div>
        {hint && <div className="text-small text-text-secondary mt-0.5 leading-4">{hint}</div>}
      </div>
      <div>{children}</div>
    </div>
  );
}

function NumberField({ label, value, min, max, unit, disabled, onChange }: {
  label: string; value: number; min: number; max: number; unit: string; disabled: boolean; onChange: (v: number) => void;
}) {
  return (
    <span className="flex items-center gap-2">
      <input type="number" aria-label={label} className={`${INPUT} w-24`} min={min} max={max} value={value} disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))} />
      <span className="text-small text-text-secondary">{unit}</span>
    </span>
  );
}

function Loading({ error }: { error: string | null }) {
  return error
    ? <Banner tone="error" className="m-5">{error}</Banner>
    : <div className="p-5 flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div>;
}

// ---------------------------------------------------------------- General and Approvals share one form

function useSettingsForm() {
  const { setMe } = useAuth();
  const { data, error, reload } = useLoad(api.settings, []);
  const [draft, setDraft] = useState<Partial<SettingsValues>>({});
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const values: SettingsValues | null = data ? { ...data.values, ...draft } : null;
  const changed = data ? (Object.keys(draft) as (keyof SettingsValues)[]).filter((k) => draft[k] !== data.values[k]) : [];

  function set<K extends keyof SettingsValues>(key: K, value: SettingsValues[K]) {
    setSaved(false);
    setDraft((d) => ({ ...d, [key]: value }));
  }

  async function save() {
    setBusy(true);
    setSaved(false);
    setFailure(null);
    try {
      await api.updateSettings(Object.fromEntries(changed.map((k) => [k, draft[k]])) as Partial<SettingsValues>);
      setDraft({});
      await reload();
      setMe(await api.me());  // switches such as Messaging change the menu straight away
      setSaved(true);
    } catch (e) {
      setFailure(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return { data, error, values, set, dirty: changed.length > 0, busy, saved, failure, save };
}

type SettingsForm = ReturnType<typeof useSettingsForm>;

function SaveBar({ form }: { form: SettingsForm }) {
  const { can } = useAuth();
  const updated = form.data?.updated;
  return (
    <>
      {form.failure && <Banner tone="error" className="mx-5 mt-4">{form.failure}</Banner>}
      <div className="px-5 py-3 flex items-center gap-3 bg-surface">
        <span className="flex-1 text-small text-text-secondary">
          {updated ? `Last changed by ${updated.by}, ${formatDateTime(updated.at)}.` : "Defaults; nothing has been changed yet."}
        </span>
        {form.saved && <span className="text-small text-success font-semibold">Saved</span>}
        {can("settings.manage")
          ? <Button variant="primary" disabled={!form.dirty || form.busy} onClick={() => void form.save()}>{form.busy && <Spinner />}Save</Button>
          : <span className="text-small text-text-secondary">Only an Admin can change these.</span>}
      </div>
    </>
  );
}

function GeneralPanel() {
  const { can } = useAuth();
  const form = useSettingsForm();
  const locked = !can("settings.manage");
  if (!form.values) return <Loading error={form.error} />;
  const v = form.values;
  return (
    <>
      <PanelHead title="General" subtitle="Applies to everyone who signs in to this Fleet Control." />
      <Row label="Workspace name" hint="Shown under the product name in the top bar.">
        <input className={INPUT} aria-label="Workspace name" maxLength={80} value={v.workspace_name} disabled={locked}
          onChange={(e) => form.set("workspace_name", e.target.value)} />
      </Row>
      <Row label="Sign-in lasts" hint="Without activity, before signing in again. Applies to new sign-ins.">
        <NumberField label="Sign-in hours" value={v.session_hours} min={1} max={24} unit="hours (1–24)" disabled={locked}
          onChange={(n) => form.set("session_hours", n)} />
      </Row>
      <Row label="Messaging" hint="Deliver fleet events through the instances' messaging gateways. Off hides Messaging from the menu and sends nothing; channels and rules are kept.">
        <label className="flex items-center gap-2 text-[13px] cursor-pointer">
          <input type="checkbox" className="accent-primary" disabled={locked} checked={v.messaging_enabled}
            onChange={(e) => form.set("messaging_enabled", e.target.checked)} />
          Messaging on
        </label>
      </Row>
      <Row label="Portal address" hint="Where links in messages point (Messaging): the address people open Fleet Control at.">
        <input className={INPUT} aria-label="Portal address" maxLength={200} value={v.portal_url} disabled={locked}
          placeholder="https://fleetcontrol.example.com" onChange={(e) => form.set("portal_url", e.target.value)} />
      </Row>
      <Row label="Longest API token" hint="The most days a new API token may stay valid.">
        <NumberField label="Longest API token in days" value={v.token_max_days} min={1} max={365} unit="days (1–365)" disabled={locked}
          onChange={(n) => form.set("token_max_days", n)} />
      </Row>
      <SaveBar form={form} />
    </>
  );
}

function ApprovalsPanel() {
  const { can } = useAuth();
  const form = useSettingsForm();
  const locked = !can("settings.manage");
  if (!form.values) return <Loading error={form.error} />;
  const v = form.values;
  return (
    <>
      <PanelHead title="Approvals" subtitle="How many people must approve a plan before it can be applied." />
      <Row label="Production" hint="Admins and Approvers approve; never the plan's creator, and one approval per person.">
        <NumberField label="Production approvals" value={v.approvals_production} min={1} max={5} unit="approvals (1–5)" disabled={locked}
          onChange={(n) => form.set("approvals_production", n)} />
      </Row>
      <Row label="Staging" hint="Admins, Fleet Architects and Operators approve.">
        <NumberField label="Staging approvals" value={v.approvals_staging} min={0} max={5} unit="approvals (0–5)" disabled={locked}
          onChange={(n) => form.set("approvals_staging", n)} />
      </Row>
      <Row label="Lab" hint="Admins, Fleet Architects and Operators approve.">
        <NumberField label="Lab approvals" value={v.approvals_lab} min={0} max={5} unit="approvals (0–5)" disabled={locked}
          onChange={(n) => form.set("approvals_lab", n)} />
      </Row>
      <Row label="Tests before production" hint="A blueprint's agent tests must have passed on lab or staging before a production apply (Test Lab).">
        <label className="flex items-center gap-2 text-[13px] cursor-pointer">
          <input type="checkbox" className="accent-primary" disabled={locked} checked={v.require_tests_for_production}
            onChange={(e) => form.set("require_tests_for_production", e.target.checked)} />
          Require a passing test suite
        </label>
      </Row>
      <div className="px-5 py-3 border-b border-hairline text-small text-text-secondary">
        A blueprint target can ask for more approvals, never fewer. Changes apply to plans created from now on.
      </div>
      <SaveBar form={form} />
    </>
  );
}

// ---------------------------------------------------------------- API tokens

function TokensPanel() {
  const { can } = useAuth();
  const now = useNow();
  const [everyone, setEveryone] = useState(false);
  const { data: tokens, error, reload } = useLoad(() => api.tokens(everyone), [everyone]);
  const { data: settings } = useLoad(() => (can("settings.read") ? api.settings() : Promise.resolve(null)), []);
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<{ name: string; secret: string } | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function revoke(t: ApiToken) {
    if (!window.confirm(`Revoke "${t.name}"? Anything using it stops working at once.`)) return;
    setFailure(null);
    try {
      await api.revokeToken(t.id);
      await reload();
    } catch (e) {
      setFailure(errorText(e));
    }
  }

  return (
    <>
      <PanelHead title="API tokens" subtitle="For scripts and CI calling the Fleet Control API. A token acts as you, with your role, and never more." />
      <div className="px-5 py-4 border-b border-hairline flex items-center gap-4">
        <p className="m-0 flex-1 text-small text-text-secondary">
          Send it as <Mono>Authorization: Bearer fct_…</Mono>. Tokens cannot create tokens or change your password; Fleet Control keeps only a hash.
        </p>
        <Button variant="primary" icon="plus" onClick={() => setCreating(true)}>New token</Button>
      </div>
      {can("users.manage") && (
        <label className="flex items-center gap-2 px-5 py-2.5 border-b border-hairline text-small cursor-pointer">
          <input type="checkbox" className="accent-primary" checked={everyone} onChange={(e) => setEveryone(e.target.checked)} />
          Show everyone's tokens (Admin)
        </label>
      )}
      {error && <Banner tone="error" className="m-4">{error}</Banner>}
      {failure && <Banner tone="error" className="m-4">{failure}</Banner>}
      {!tokens && !error && <Loading error={null} />}
      {tokens && tokens.length === 0 && <p className="m-0 px-5 py-6 text-[13px] text-text-secondary">No tokens yet.</p>}
      {tokens?.map((t) => {
        const state = tokenState(t, now);
        return (
          <div key={t.id} className="grid grid-cols-[minmax(0,1fr)_150px_90px_auto] gap-3 px-5 py-3 border-b border-hairline items-center text-[13px]">
            <div className="min-w-0">
              <div className="font-semibold truncate">{t.name}</div>
              <div className="text-small text-text-secondary truncate">
                {everyone ? `${t.owner} · ` : ""}created {formatDate(t.created_at)} · {t.last_used_at ? `last used ${formatDateTime(t.last_used_at)}` : "never used"}
              </div>
            </div>
            <div className="text-small text-text-secondary">{t.revoked_at ? `Revoked ${formatDate(t.revoked_at)}` : `Expires ${formatDate(t.expires_at)}`}</div>
            <div><Chip tone={state.tone}>{state.label}</Chip></div>
            <div className="flex justify-end">{state.label === "Active" && <Button variant="danger" onClick={() => void revoke(t)}>Revoke</Button>}</div>
          </div>
        );
      })}
      {creating && (
        <NewTokenModal maxDays={settings?.values.token_max_days ?? 90} onClose={() => setCreating(false)}
          onCreated={(name, value) => { setCreating(false); setSecret({ name, secret: value }); void reload(); }} />
      )}
      {secret && <TokenSecretModal name={secret.name} secret={secret.secret} onClose={() => setSecret(null)} />}
    </>
  );
}

function NewTokenModal({ maxDays, onClose, onCreated }: { maxDays: number; onClose: () => void; onCreated: (name: string, secret: string) => void }) {
  const choices = lifetimeChoices(maxDays);
  const [name, setName] = useState("");
  const [days, setDays] = useState(choices.includes(30) ? 30 : choices[choices.length - 1]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.createToken({ name: name.trim(), expires_days: days });
      onCreated(r.token.name, r.secret);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <Modal title="New API token" width={480} onClose={onClose} footer={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button variant="primary" type="submit" form="token-form" disabled={busy || !name.trim()}>{busy && <Spinner />}Create token</Button>
    </>}>
      <form id="token-form" onSubmit={(e) => void submit(e)}>
        <Field label="Name" hint="What will use it, e.g. “CI deploy pipeline”.">
          <input className={INPUT} autoFocus maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Expires after">
          <select className={INPUT} value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {choices.map((d) => <option key={d} value={d}>{d} days</option>)}
          </select>
        </Field>
        <p className="text-small text-text-secondary">It can do what your role can do, as you. It cannot create tokens or change your password.</p>
        {error && <Banner tone="error">{error}</Banner>}
      </form>
    </Modal>
  );
}

function TokenSecretModal({ name, secret, onClose }: { name: string; secret: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <Modal title={`Token “${name}” created`} subtitle="Copy it now: Fleet Control keeps only a hash and cannot show it again." onClose={onClose}
      footer={<Button variant="primary" onClick={onClose}>Done</Button>}>
      <div className="flex items-center gap-2">
        <code className="flex-1 font-mono text-[13px] px-3 py-2.5 rounded-control bg-container-low border border-hairline select-all break-all">{secret}</code>
        <Button icon="copy" onClick={() => { void navigator.clipboard.writeText(secret); setCopied(true); }}>{copied ? "Copied" : "Copy"}</Button>
      </div>
      <p className="text-small text-text-secondary mt-4 mb-0">
        Example: <Mono>curl -H "Authorization: Bearer $FC_TOKEN" http://…/api/v1/instances</Mono>
      </p>
    </Modal>
  );
}

// ---------------------------------------------------------------- where Assurance gets its evidence

function ObservabilityPanel() {
  const { data: instances, error } = useLoad(api.instances, [], 15_000);
  if (!instances) return <Loading error={error} />;
  return (
    <>
      <PanelHead title="Observability" subtitle="Where the evidence behind Assurance verdicts comes from, instance by instance." />
      <div className="px-5 py-4 border-b border-hairline text-[13px] leading-5 text-text-secondary">
        Fleet Control reads each run's tool calls from the Hermes session transcript, so Assurance works on any instance with a paired
        agent. Two plugins add more: the <Mono>fleetcontrol</Mono> plugin gives real-time evidence and can block a tool call
        (<strong className="text-text">Policy blocked</strong>), and Hermes's Langfuse plugin adds traces. Both are turned on
        <em> on the instance</em>; Fleet Control never enables one behind your back.
      </div>
      {instances.length === 0 && <p className="m-0 px-5 py-6 text-[13px] text-text-secondary">No instance connected yet.</p>}
      {instances.map((i) => {
        const paired = i.mode === "agent" && Boolean(i.agent_version);
        const plugin = i.report?.plugins?.fleetcontrol ?? (paired ? "unknown" : "no agent");
        const langfuse = i.report?.plugins?.langfuse ?? "unknown";
        return (
          <div key={i.id} className="px-5 py-3.5 border-b border-hairline flex flex-col gap-1.5">
            <div className="flex items-center justify-between gap-3">
              <span className="font-semibold">{i.id} <span className="font-normal text-small text-text-secondary">{ENV_LABEL[i.environment]}</span></span>
              <Chip tone={paired ? "success" : "neutral"}>{paired ? "Session transcript" : "No evidence source"}</Chip>
            </div>
            <div className="text-small text-text-secondary flex flex-wrap gap-x-5 gap-y-1">
              <span>Fleet Control plugin: <strong className="text-text">{plugin}</strong></span>
              <span>Langfuse: <strong className="text-text">{langfuse}</strong></span>
            </div>
            {plugin === "installed, not enabled" && (
              <span className="text-small text-text-secondary">
                Add <Mono>fleetcontrol</Mono> to <Mono>plugins.enabled</Mono> in that instance's <Mono>config.yaml</Mono> and restart Hermes
                for real-time evidence and policy blocking.
              </span>
            )}
            {!paired && <span className="text-small text-text-secondary">API-only instances have no transcript access: verdicts there stay “Not verifiable”.</span>}
          </div>
        );
      })}
      <div className="px-5 py-3 bg-surface text-small text-text-secondary">
        Langfuse also needs its Python package and keys on the instance (<Mono>HERMES_LANGFUSE_PUBLIC_KEY</Mono> and
        <Mono>HERMES_LANGFUSE_SECRET_KEY</Mono>), which stay there: Fleet Control never holds them.
      </div>
    </>
  );
}
