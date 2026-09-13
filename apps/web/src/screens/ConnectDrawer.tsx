import { useState, type FormEvent } from "react";
import { api, type CreatedInstance, type Environment, type InstanceDetail, type InstanceMode } from "../api/client";
import { errorText, useLoad } from "../lib/hooks";
import { capabilityRows } from "../lib/view";
import { Banner, Button, Chip, Drawer, Field, INPUT, Label, Mono, Spinner } from "../components/ui";

const ID_PATTERN = /^[a-z0-9][a-z0-9.-]{1,62}$/;

const PATHS: { mode: InstanceMode; title: string; body: string }[] = [
  {
    mode: "agent",
    title: "Install the Fleet Control Agent",
    body: "One command on the Hermes host. Reads and writes profiles, captures tool evidence in real time, enforces policies. Nothing is exposed inbound.",
  },
  {
    mode: "api-only",
    title: "Connect via API only (read-only)",
    body: "Nothing installed. No writes; most assurance verdicts will be Not verifiable.",
  },
];

export function ConnectDrawer({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [mode, setMode] = useState<InstanceMode>("agent");
  const [id, setId] = useState("");
  const [env, setEnv] = useState<Environment>("staging");
  const [created, setCreated] = useState<CreatedInstance | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const watching = created !== null && created.mode === "agent";
  const { data: live } = useLoad<InstanceDetail | null>(
    () => (created ? api.instance(created.id) : Promise.resolve(null)),
    [created?.id],
    watching ? 2000 : 0,
  );
  const paired = Boolean(live?.agent_version);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setCreated(await api.createInstance({ id, environment: env, mode }));
      onCreated();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function copy(text: string) {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  if (created) {
    return (
      <Drawer title={`Connect ${created.id}`} onClose={onClose} footer={<Button variant="primary" onClick={onClose}>Done</Button>}>
        {created.mode === "agent" ? (
          <>
            <Label className="mb-2">1 · Run on the Hermes host</Label>
            <p className="mt-0 mb-2 text-text-secondary">As the user that runs Hermes. Replace <Mono>&lt;this server&gt;</Mono> with this Fleet Control's address.</p>
            <pre className="m-0 p-3 rounded-control bg-container-low border border-hairline font-mono text-[12px] whitespace-pre-wrap break-all">{created.install_command}</pre>
            <div className="flex items-center gap-3 mt-2 mb-1">
              <Button icon="copy" onClick={() => void copy(created.install_command)}>{copied ? "Copied" : "Copy command"}</Button>
            </div>
            <p className="text-small text-text-secondary mt-1 mb-6">The pairing token in it works once and is not shown again.</p>

            <Label className="mb-2">2 · Pairing</Label>
            {paired && live ? (
              <>
                <Banner tone="success" className="mb-4">
                  Paired · Hermes {live.hermes_version ?? "version unknown"} · agent {live.agent_version}
                </Banner>
                <Label className="mb-2">What Fleet Control can do here</Label>
                <div className="border border-hairline rounded-control divide-y divide-hairline">
                  {capabilityRows(live).map((r) => (
                    <div key={r.label} className="flex justify-between gap-3 px-3 py-2.5">
                      <span>{r.label}</span>
                      <span className={`text-small font-semibold ${r.tone === "success" ? "text-success" : r.tone === "warning" ? "text-warning" : "text-text-secondary"}`}>{r.value}</span>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <div className="flex items-center gap-2 text-text-secondary"><Spinner /> Waiting for the agent to pair…</div>
            )}
          </>
        ) : (
          <Banner tone="info">
            {created.id} is recorded as a read-only instance. Discovery over the Hermes <Mono>/v1</Mono> API (endpoint and API key)
            is not wired into this build yet, so it shows as Not checked; nothing on the host is changed.
          </Banner>
        )}
      </Drawer>
    );
  }

  return (
    <Drawer title="Connect a Hermes instance" onClose={onClose}
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="connect-form" disabled={!ID_PATTERN.test(id) || busy}>
          {busy ? <Spinner /> : null}{mode === "agent" ? "Create and get install command" : "Connect read-only"}
        </Button>
      </>}>
      <form id="connect-form" onSubmit={(e) => void submit(e)}>
        <Label className="mb-2">How should Fleet Control connect?</Label>
        <div className="flex flex-col gap-2 mb-5">
          {PATHS.map((p) => (
            <label key={p.mode} className={`flex gap-3 p-3 rounded-control border cursor-pointer ${mode === p.mode ? "border-primary bg-container-low" : "border-border"}`}>
              <input type="radio" name="mode" aria-label={p.title} className="mt-1 accent-primary" checked={mode === p.mode} onChange={() => setMode(p.mode)} />
              <span>
                <span className="font-semibold flex items-center gap-2">{p.title}{p.mode === "agent" && <Chip tone="info">Recommended</Chip>}</span>
                <span className="block text-small text-text-secondary mt-0.5">{p.body}</span>
              </span>
            </label>
          ))}
        </div>
        <Field label="Instance ID" hint="Lowercase letters, digits, dots and hyphens, e.g. hermes-staging-eu-01.">
          <input className={INPUT} value={id} onChange={(e) => setId(e.target.value.trim())} placeholder="hermes-staging-eu-01" autoFocus />
        </Field>
        <Field label="Environment" hint="Production applies need two approvals; lab and staging need none.">
          <select className={INPUT} value={env} onChange={(e) => setEnv(e.target.value as Environment)}>
            <option value="lab">Lab</option>
            <option value="staging">Staging</option>
            <option value="production">Production</option>
          </select>
        </Field>
        {error && <Banner tone="error">{error}</Banner>}
      </form>
    </Drawer>
  );
}
