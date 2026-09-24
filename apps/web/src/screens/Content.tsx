import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import { api, type Classification, type ContentFile, type ContentZone, type RoleName } from "../api/client";
import { useAuth } from "../lib/auth";
import { errorText, useLoad } from "../lib/hooks";
import { CLASSIFICATIONS, CLASSIFICATION_LABEL, CLASSIFICATION_TONE, fileIcon, formatSize, zoneIdFrom } from "../lib/content";
import { formatDateTime, prettyId } from "../lib/view";
import { Banner, Button, Card, Chip, Field, INPUT, Icon, Modal, Mono, PageHeader, Spinner, TEXTAREA } from "../components/ui";

const ROLE_OPTIONS: RoleName[] = ["admin", "fleet_architect", "operator", "approver", "viewer"];

// design/screens/Content: zones · files · access
export function ContentScreen() {
  const { can } = useAuth();
  const manage = can("content.manage");
  const { data: zones, error, reload } = useLoad(api.contentZones, [], 15_000);
  const [search] = useSearchParams();  // ?zone=&file= open one file (Ask the fleet links here)
  const [selected, setSelected] = useState<string | null>(search.get("zone"));
  const [creating, setCreating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const zone = (zones ?? []).find((z) => z.id === selected) ?? zones?.[0] ?? null;
  const { data: files, reload: reloadFiles } = useLoad(() => (zone ? api.contentFiles(zone.id) : Promise.resolve([])), [zone?.id]);

  return (
    <>
      <PageHeader crumb="Library" title="Content"
        subtitle="Files agents may read. Access is set per zone: for people by role, for agents by the blueprint."
        actions={manage && <>
          <Button icon="plus" onClick={() => setCreating(true)}>New zone</Button>
          <Button variant="primary" icon="download" disabled={!zone || zone.managed} onClick={() => setUploading(true)}
            title={zone?.managed ? "Managed zones are synced from their source" : undefined}>Upload</Button>
        </>} />
      {error && <Banner tone="error" className="mb-4">{error}</Banner>}
      {msg && <Banner tone="info" className="mb-4">{msg}</Banner>}

      <div className="flex gap-5 items-start">
        <Card className="w-[240px] shrink-0 overflow-hidden">
          <div className="px-4 pt-3.5 pb-2.5 text-[15px] font-semibold">Zones</div>
          <div className="flex flex-col gap-0.5 px-2 pb-2.5">
            {!zones && <div className="px-2.5 py-2 flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div>}
            {zones?.length === 0 && <p className="m-0 px-2.5 py-2 text-small text-text-secondary">No zone yet.</p>}
            {zones?.map((z) => (
              <button key={z.id} type="button" onClick={() => setSelected(z.id)}
                className={`h-[34px] flex items-center justify-between gap-2.5 px-2.5 rounded-control text-[13px] text-left cursor-pointer ${zone?.id === z.id ? "bg-primary-tint text-primary font-semibold" : "font-medium hover:bg-container-low"}`}>
                <span className="flex items-center gap-2 min-w-0">
                  <Icon name="folder" size={16} />
                  <span className="truncate">{z.name}</span>
                  {z.managed && <span className="text-text-secondary font-normal">(managed)</span>}
                </span>
                <span className={`text-[12px] ${zone?.id === z.id ? "" : "text-text-secondary"}`}>{z.files}</span>
              </button>
            ))}
          </div>
          <div className="px-4 py-3 border-t border-hairline text-small text-text-secondary">
            Managed zones are synced from an MCP source and are not edited here.
          </div>
        </Card>

        <Card className="flex-1 min-w-0 overflow-hidden">
          <div className="flex items-center gap-3 px-4 py-3 border-b border-hairline">
            <span className="text-[15px] font-semibold">{zone ? zone.name : "Files"}</span>
            {zone && !zone.may_read && <Chip tone="neutral">Your role does not read this zone</Chip>}
            <div className="flex-1" />
            <span className="text-small text-text-secondary">{files ? `${files.length} file${files.length === 1 ? "" : "s"}` : ""}</span>
          </div>
          <div className="grid grid-cols-[2.6fr_1fr_0.9fr_auto] gap-3 px-4 py-2.5 border-b border-hairline text-label uppercase text-text-secondary">
            <div>Name</div><div>Classification</div><div>Added</div><div />
          </div>
          {!files && zone && <div className="px-4 py-4 flex gap-2 items-center text-text-secondary"><Spinner /> Loading files…</div>}
          {files?.length === 0 && (
            <p className="m-0 px-4 py-6 text-[13px] text-text-secondary">
              {zone?.may_read ? "No file in this zone yet." : "Files here are not shown to your role."}
            </p>
          )}
          {files?.map((f) => (
            <FileRow key={f.id} file={f} startOpen={f.id === search.get("file")} manage={manage && !zone?.managed}
              onRemoved={async () => { await reloadFiles(); await reload(); setMsg(`Removed ${f.name}.`); }} />
          ))}
        </Card>

        {zone && <AccessRail zone={zone} manage={manage} onSaved={async () => { await reload(); setMsg("Access updated."); }} />}
      </div>

      {creating && <ZoneModal onClose={() => setCreating(false)}
        onCreated={async (z) => { setCreating(false); setSelected(z.id); await reload(); setMsg(`Zone ${z.name} created.`); }} />}
      {uploading && zone && <UploadModal zone={zone} onClose={() => setUploading(false)}
        onDone={async (name) => { setUploading(false); await reloadFiles(); await reload(); setMsg(`${name} added to ${zone.name}.`); }} />}
    </>
  );
}

function FileRow({ file, manage, onRemoved, startOpen = false }: { file: ContentFile; manage: boolean; onRemoved: () => Promise<void>; startOpen?: boolean }) {
  const [open, setOpen] = useState(startOpen);
  const [busy, setBusy] = useState(false);
  return (
    <>
      <div className="grid grid-cols-[2.6fr_1fr_0.9fr_auto] gap-3 px-4 py-3 border-b border-hairline items-center">
        <button type="button" onClick={() => setOpen(true)} className="flex items-center gap-2.5 min-w-0 text-left cursor-pointer bg-transparent">
          <Icon name={fileIcon(file.name)} className="text-text-secondary shrink-0" />
          <span className="font-medium truncate">{file.name}</span>
          <span className="text-small text-text-secondary shrink-0">{formatSize(file.size)}</span>
        </button>
        <div><Chip tone={CLASSIFICATION_TONE[file.classification]}>{CLASSIFICATION_LABEL[file.classification]}</Chip></div>
        <div className="text-small text-text-secondary">{formatDateTime(file.at)}</div>
        <div className="flex justify-end">
          {manage && (
            <Button variant="danger" disabled={busy}
              onClick={() => { if (window.confirm(`Remove ${file.name}?`)) { setBusy(true); void onRemoved().finally(() => setBusy(false)); void api.deleteFile(file.id); } }}>
              Remove
            </Button>
          )}
        </div>
      </div>
      {open && <FileModal id={file.id} onClose={() => setOpen(false)} />}
    </>
  );
}

function FileModal({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: file, error } = useLoad(() => api.contentFile(id), [id]);
  return (
    <Modal width={720} title={file?.name ?? "File"} onClose={onClose}
      subtitle={file ? `${CLASSIFICATION_LABEL[file.classification]} · added by ${file.uploaded_by}, ${formatDateTime(file.at)}` : undefined}
      footer={<Button variant="primary" onClick={onClose}>Close</Button>}>
      {error && <Banner tone="error">{error}</Banner>}
      {!file && !error && <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div>}
      {file && (file.text
        ? <pre className="m-0 p-3 bg-container-low rounded-control whitespace-pre-wrap break-words font-mono text-[12px] max-h-[420px] overflow-auto">{file.text}</pre>
        : <p className="m-0 text-text-secondary">This file was added as metadata only: its content lives on the instance.</p>)}
    </Modal>
  );
}

function AccessRail({ zone, manage, onSaved }: { zone: ContentZone; manage: boolean; onSaved: () => Promise<void> }) {
  const [roles, setRoles] = useState<RoleName[]>(zone.read_roles);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const dirty = roles.join() !== zone.read_roles.join();
  const editable = manage && !zone.managed;

  async function save() {
    setBusy(true);
    setFailure(null);
    try {
      await api.editZone(zone.id, { read_roles: roles });
      await onSaved();
    } catch (e) {
      setFailure(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="w-[320px] shrink-0 p-[18px] flex flex-col gap-3.5">
      <div>
        <div className="text-[15px] font-semibold">Who can read {zone.name}</div>
        <div className="text-small text-text-secondary">Set separately for people and for agents.</div>
      </div>

      <div className="flex flex-col gap-2">
        <div className="text-label uppercase text-text-secondary">People</div>
        <div className="border border-hairline rounded-control divide-y divide-hairline">
          <div className="flex items-center gap-2.5 px-3 py-2.5 text-[13px] text-text-secondary">
            <Icon name="lock" size={16} />Admins always read every zone
          </div>
          {ROLE_OPTIONS.filter((r) => r !== "admin").map((r) => (
            <label key={r} className={`flex items-center gap-2.5 px-3 py-2.5 text-[13px] ${editable ? "cursor-pointer" : ""}`}>
              <input type="checkbox" className="accent-primary" disabled={!editable} checked={roles.includes(r)}
                onChange={(e) => setRoles(e.target.checked ? [...roles, r] : roles.filter((x) => x !== r))} />
              {prettyId(r)}s
            </label>
          ))}
        </div>
        {editable && (
          <div className="flex justify-end">
            <Button variant="primary" disabled={!dirty || busy} onClick={() => void save()}>{busy && <Spinner />}Save access</Button>
          </div>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <div className="text-label uppercase text-text-secondary">Agents</div>
        {zone.agents.length === 0 ? (
          <p className="m-0 text-small text-text-secondary">No blueprint agent lists this zone in its content zones.</p>
        ) : (
          <div className="border border-hairline rounded-control divide-y divide-hairline">
            {zone.agents.map((a) => (
              <div key={`${a.blueprint}:${a.agent}`} className="flex items-start gap-2.5 px-3 py-2.5 text-[13px]">
                <Icon name="bot" size={16} className="text-secondary shrink-0 mt-0.5" />
                <span className="flex-1 min-w-0">
                  <span className="block truncate">{a.agent}</span>
                  <span className="block text-small text-text-secondary">{a.blueprint}</span>
                  {a.redacted_only && (
                    <span className="flex items-center gap-1 text-small text-warning mt-0.5"><Icon name="warning" size={12} />Redacted summaries only</span>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}
        <p className="m-0 text-small text-text-secondary">
          Agents reach a zone through the blueprint (<Mono>content_zones</Mono>), so it changes through a plan. Redaction is applied by the
          instance before the agent sees the file.
        </p>
      </div>
      {zone.managed && <Banner tone="info" className="text-small">Synced from {zone.source ?? "an external source"}; access and files are managed there.</Banner>}
      {failure && <Banner tone="error">{failure}</Banner>}
    </Card>
  );
}

function ZoneModal({ onClose, onCreated }: { onClose: () => void; onCreated: (z: ContentZone) => Promise<void> }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [roles, setRoles] = useState<RoleName[]>(["approver"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = zoneIdFrom(name);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onCreated(await api.createZone({ id, name: name.trim(), description: description.trim(), read_roles: roles }));
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <Modal width={520} title="New content zone" subtitle="A zone is the unit of access: people reach it by role, agents through the blueprint." onClose={onClose}
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="zone-form" disabled={busy || !id}>{busy && <Spinner />}Create zone</Button>
      </>}>
      <form id="zone-form" onSubmit={(e) => void submit(e)}>
        <Field label="Name" hint={id ? <>Zone id: <Mono>{id}</Mono></> : "Lowercase letters, digits and hyphens once turned into an id."}>
          <input className={INPUT} autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Case files" />
        </Field>
        <Field label="Description">
          <input className={INPUT} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Investigation material" />
        </Field>
        <Field label="People who may read it" hint="Admins always may.">
          <div className="flex flex-wrap gap-3">
            {ROLE_OPTIONS.filter((r) => r !== "admin").map((r) => (
              <label key={r} className="flex items-center gap-2 text-[13px] cursor-pointer">
                <input type="checkbox" className="accent-primary" checked={roles.includes(r)}
                  onChange={(e) => setRoles(e.target.checked ? [...roles, r] : roles.filter((x) => x !== r))} />
                {prettyId(r)}s
              </label>
            ))}
          </div>
        </Field>
        {error && <Banner tone="error">{error}</Banner>}
      </form>
    </Modal>
  );
}

function UploadModal({ zone, onClose, onDone }: { zone: ContentZone; onClose: () => void; onDone: (name: string) => Promise<void> }) {
  const [name, setName] = useState("");
  const [classification, setClassification] = useState<Classification>("internal");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.uploadFile({ zone: zone.id, name: name.trim(), classification, text: text || undefined });
      await onDone(name.trim());
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <Modal width={620} title={`Add a file to ${zone.name}`} onClose={onClose}
      subtitle="Text and structured files are stored here so agents and Ask the fleet can use them; binaries stay on the instance."
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="upload-form" disabled={busy || !name.trim()}>{busy && <Spinner />}Add file</Button>
      </>}>
      <form id="upload-form" onSubmit={(e) => void submit(e)}>
        <Field label="File name">
          <input className={INPUT} autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="AML-2026-0412 wire log.csv" />
        </Field>
        <Field label="Classification">
          <select className={INPUT} value={classification} onChange={(e) => setClassification(e.target.value as Classification)}>
            {CLASSIFICATIONS.map((c) => <option key={c} value={c}>{CLASSIFICATION_LABEL[c]}</option>)}
          </select>
        </Field>
        <Field label="Content" hint="Leave empty to record the file as metadata only.">
          <textarea className={`${TEXTAREA} min-h-[160px] font-mono text-[12px]`} value={text} onChange={(e) => setText(e.target.value)} />
        </Field>
        {error && <Banner tone="error">{error}</Banner>}
      </form>
    </Modal>
  );
}
