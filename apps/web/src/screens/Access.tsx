import { useState, type FormEvent } from "react";
import { api, type AgentDoc, type BlueprintDoc, type Person, type RoleName } from "../api/client";
import { useMe } from "../lib/auth";
import { errorText, useLoad } from "../lib/hooks";
import { formatDateTime, initials, prettyId } from "../lib/view";
import { Banner, Button, Card, Chip, Drawer, Field, INPUT, Icon, Modal, PageHeader, Spinner } from "../components/ui";

const ROLE_OPTIONS: { value: RoleName; label: string }[] = [
  { value: "admin", label: "Admin" },
  { value: "fleet_architect", label: "Fleet Architect" },
  { value: "operator", label: "Operator" },
  { value: "approver", label: "Approver" },
  { value: "viewer", label: "Viewer" },
];

interface Secret { email: string; password: string; why: string }

export function AccessScreen() {
  const me = useMe();
  const { data: roles, reload: reloadRoles } = useLoad(api.roles, []);
  const { data: people, error, reload } = useLoad(api.users, []);
  const { data: blueprints } = useLoad(api.blueprints, []);
  const latest = blueprints?.[0] ?? null;
  const { data: detail } = useLoad(() => (latest ? api.blueprint(latest.name, latest.latest) : Promise.resolve(null)), [latest?.name, latest?.latest]);
  const [inviting, setInviting] = useState(false);
  const [secret, setSecret] = useState<Secret | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function act(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    setFailure(null);
    try {
      await fn();
      await Promise.all([reload(), reloadRoles()]);
    } catch (e) {
      setFailure(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <PageHeader crumb="Govern" title="Access"
        subtitle="Effective access is what the person may do, intersected with what the agent may do, intersected with what the connected system allows."
        actions={<Button variant="primary" icon="plus" onClick={() => setInviting(true)}>Invite person</Button>} />
      {failure && <Banner tone="error" className="mb-4">{failure}</Banner>}

      <Card className="p-5 mb-5">
        <h2 className="text-section m-0">Check effective access</h2>
        <p className="text-small text-text-secondary mt-1 mb-0">
          The person ∩ agent ∩ system inspector arrives with the Workspace (Slice 4). Today people carry one of five roles, and
          agents carry their own permissions in the blueprint (below); connected systems keep their own authentication.
        </p>
      </Card>

      <div className="grid grid-cols-2 gap-5 mb-5 items-start">
        <Card className="overflow-hidden">
          <div className="flex items-center justify-between px-5 py-4 border-b border-hairline">
            <h2 className="text-section m-0">Roles</h2>
            <span className="text-small text-text-secondary">{people ? `${people.filter((p) => !p.disabled).length} people` : ""}</span>
          </div>
          <div className="grid grid-cols-[140px_minmax(0,1fr)_70px] gap-3 px-5 py-2.5 bg-container-low text-label uppercase text-text-secondary">
            <div>Role</div><div>What it allows</div><div className="text-right">Members</div>
          </div>
          {(roles ?? []).map((r) => (
            <div key={r.role} className="grid grid-cols-[140px_minmax(0,1fr)_70px] gap-3 px-5 py-3 border-t border-hairline items-center text-[13px]">
              <div><div className="font-semibold">{r.label}</div><div className="text-small text-text-secondary">{r.portal === "admin" ? "Admin portal" : "Workspace"}</div></div>
              <div>{r.description}</div>
              <div className="text-right num">{r.members}</div>
            </div>
          ))}
        </Card>

        <Card className="overflow-hidden">
          <div className="flex items-center justify-between px-5 py-4 border-b border-hairline">
            <h2 className="text-section m-0">Agent permissions</h2>
            <span className="text-small text-text-secondary">{detail ? `${prettyId(detail.name)} v${detail.version}` : "No blueprint yet"}</span>
          </div>
          <div className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-3 px-5 py-2.5 bg-container-low text-label uppercase text-text-secondary">
            <div>Agent</div><div>Content zones</div><div>MCPs</div><div>External actions</div>
          </div>
          {detail?.parsed.agents.map((a) => (
            <div key={a.id} className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-3 px-5 py-3 border-t border-hairline items-center text-[13px]">
              <div className="font-semibold">{prettyId(a.id)}</div>
              <div>{a.content_zones.join(", ") || <span className="text-outline">None</span>}</div>
              <div>{a.mcps.join(", ") || <span className="text-outline">None</span>}</div>
              <div><ExternalActions doc={detail.parsed} agent={a} /></div>
            </div>
          ))}
          {!detail && <p className="m-0 px-5 py-4 text-text-secondary text-[13px]">Import a blueprint to see what each agent may reach.</p>}
        </Card>
      </div>

      <Card className="overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-hairline">
          <h2 className="text-section m-0">People</h2>
          <span className="text-small text-text-secondary">Local accounts; single sign-on (OIDC) maps onto the same roles later.</span>
        </div>
        {error && <Banner tone="error" className="m-4">{error}</Banner>}
        {!people && !error && <div className="px-5 py-4 flex gap-2 items-center text-text-secondary"><Spinner /> Loading people…</div>}
        {people && (
          <>
            <div className="grid grid-cols-[minmax(0,1.6fr)_200px_110px_150px_auto] gap-3 px-5 py-2.5 bg-container-low text-label uppercase text-text-secondary">
              <div>Person</div><div>Role</div><div>Status</div><div>Last sign-in</div><div />
            </div>
            {people.map((p: Person) => {
              const self = p.email === me.email;
              return (
                <div key={p.email} className={`grid grid-cols-[minmax(0,1.6fr)_200px_110px_150px_auto] gap-3 px-5 py-3 border-t border-hairline items-center text-[13px] ${p.disabled ? "opacity-60" : ""}`}>
                  <div className="flex items-center gap-2.5 min-w-0">
                    <span className="size-7 shrink-0 rounded-full bg-secondary-tint text-secondary text-[11px] font-bold flex items-center justify-center">{initials(p.name || p.email)}</span>
                    <div className="min-w-0"><div className="font-semibold truncate">{p.name}{self && <span className="text-text-secondary font-normal"> (you)</span>}</div><div className="text-small text-text-secondary truncate">{p.email}</div></div>
                  </div>
                  <select className={`${INPUT} h-8`} aria-label={`Role of ${p.email}`} value={p.role} disabled={self || busy !== null}
                    title={self ? "You cannot change your own role" : undefined}
                    onChange={(e) => void act(`role:${p.email}`, () => api.updateUser(p.email, { role: e.target.value as RoleName }))}>
                    {ROLE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                  <div><Chip tone={p.disabled ? "neutral" : "success"}>{p.disabled ? "Disabled" : "Active"}</Chip></div>
                  <div className="text-text-secondary">{p.last_login ? formatDateTime(p.last_login) : "Never"}</div>
                  <div className="flex gap-2 justify-end">
                    <Button disabled={busy !== null} onClick={() => void act(`reset:${p.email}`, async () => {
                      const r = await api.resetPassword(p.email);
                      setSecret({ email: p.email, password: r.password, why: "Password reset. Their sessions have ended." });
                    })}>Reset password</Button>
                    <Button variant={p.disabled ? "secondary" : "danger"} disabled={self || busy !== null} title={self ? "You cannot disable yourself" : undefined}
                      onClick={() => void act(`dis:${p.email}`, () => api.updateUser(p.email, { disabled: !p.disabled }))}>
                      {p.disabled ? "Enable" : "Disable"}
                    </Button>
                  </div>
                </div>
              );
            })}
          </>
        )}
      </Card>

      {inviting && <InviteDrawer onClose={() => setInviting(false)} onInvited={(email, password) => {
        setInviting(false);
        setSecret({ email, password, why: "Account created." });
        void reload();
        void reloadRoles();
      }} />}
      {secret && <SecretModal secret={secret} onClose={() => setSecret(null)} />}
    </>
  );
}

function ExternalActions({ doc, agent }: { doc: BlueprintDoc; agent: AgentDoc }) {
  const tools = doc.policies
    .filter((p) => p.enforcement === "approve" && (p.applies_to.includes("*") || p.applies_to.includes(agent.id)))
    .flatMap((p) => ((p.params?.tools as string[] | undefined) ?? []));
  if (!tools.length) return <span className="text-outline">None</span>;
  return <span title={tools.join(", ")}><Chip tone="warning">Requires approval</Chip></span>;
}

function InviteDrawer({ onClose, onInvited }: { onClose: () => void; onInvited: (email: string, password: string) => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<RoleName>("viewer");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.createUser({ email, name, role });
      onInvited(r.user.email, r.password);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <Drawer title="Invite a person" onClose={onClose} footer={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button variant="primary" type="submit" form="invite-form" disabled={busy || !email || !name}>{busy && <Spinner />}Create account</Button>
    </>}>
      <form id="invite-form" onSubmit={(e) => void submit(e)}>
        <Field label="Email"><input className={INPUT} type="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
        <Field label="Name"><input className={INPUT} required value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Role" hint="Approvers and Viewers use the Workspace; the other roles use the admin portal.">
          <select className={INPUT} value={role} onChange={(e) => setRole(e.target.value as RoleName)}>
            {ROLE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Field>
        <p className="text-small text-text-secondary">Fleet Control generates a one-time password and shows it to you once. Share it privately.</p>
        {error && <Banner tone="error">{error}</Banner>}
      </form>
    </Drawer>
  );
}

function SecretModal({ secret, onClose }: { secret: Secret; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <Modal title={`One-time password for ${secret.email}`} subtitle={secret.why} onClose={onClose} footer={<Button variant="primary" onClick={onClose}>Done</Button>}>
      <div className="flex items-center gap-2">
        <code className="flex-1 font-mono text-[14px] px-3 py-2.5 rounded-control bg-container-low border border-hairline select-all break-all">{secret.password}</code>
        <Button icon="copy" onClick={() => { void navigator.clipboard.writeText(secret.password); setCopied(true); }}>{copied ? "Copied" : "Copy"}</Button>
      </div>
      <Banner tone="warning" className="mt-4">
        <span className="flex items-center gap-1"><Icon name="lock" size={14} />Shown once; Fleet Control keeps only a hash. Ask them to change it after signing in.</span>
      </Banner>
    </Modal>
  );
}
