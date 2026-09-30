import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api, goamlXmlUrl, type GoamlProfile, type GoamlReport, type GoamlRoom, type GoamlSchemaInfo } from "../api/client";
import { useAuth } from "../lib/auth";
import { errorText, useLoad } from "../lib/hooks";
import { GOAML_STATUS, checkedAgainstOlderSchema, goamlGaps } from "../lib/goaml";
import { formatDateTime, timeAgo } from "../lib/view";
import { Banner, Button, Card, Chip, Field, INPUT, Modal, Mono, PageHeader, Spinner } from "../components/ui";

// goAML (goaml.py): a decided room's SAR draft becomes the FIU's XML. The FIU's schema decides; a person files.

/** Every goAML report the person may see (the room's content zone decides), newest first. */
export function GoamlScreen() {
  const { data: rows, error, reload } = useLoad(api.goamlReports, [], 15_000);
  const { data: settings } = useLoad(api.goamlSettings, []);
  const [open, setOpen] = useState<string | null>(null);
  const gaps = goamlGaps(settings ?? null);
  return (
    <>
      <PageHeader crumb="Workspace" title="goAML reports"
        subtitle={<>Suspicious transaction reports for {settings?.profile?.fiu ?? "the FIU"}, prepared from decided Decision Rooms. Fleet Control
          checks each against the FIU's own goAML schema; a person files it in goAML and records the reference here.</>} />
      {gaps.length > 0 && <Banner tone="warning" className="mb-4">Before reports can be checked, an Admin sets {gaps.join(" and ")}.</Banner>}
      {error && <Banner tone="error" className="mb-4">{error}</Banner>}
      {!rows ? <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div> : rows.length === 0 ? (
        <Card className="p-6 text-text-secondary">No goAML report yet. One is prepared from a Decision Room once its decision is in: open the room and use <b>Prepare goAML report</b>.</Card>
      ) : (
        <Card className="divide-y divide-hairline">
          <div className="grid grid-cols-[150px_140px_minmax(0,1fr)_170px_180px] gap-3 px-4 py-2.5 text-label uppercase text-text-secondary">
            <span>Reference</span><span>Case</span><span>Prepared</span><span>Status</span><span>FIU reference</span>
          </div>
          {rows.map((r) => (
            <button key={r.id} type="button" onClick={() => setOpen(r.id)}
              className="w-full grid grid-cols-[150px_140px_minmax(0,1fr)_170px_180px] gap-3 px-4 py-3 text-left text-[13px] items-center hover:bg-container-low cursor-pointer">
              <Mono>{r.entity_reference}</Mono>
              <span className="truncate">{r.case ?? "—"}</span>
              <span className="truncate text-text-secondary">{r.reporter.first_name} {r.reporter.last_name} · {timeAgo(r.created_at)}</span>
              <span><Chip tone={GOAML_STATUS[r.status].tone}>{GOAML_STATUS[r.status].label}</Chip></span>
              <span>{r.filed ? <Mono>{r.filed.fiu_ref_number}</Mono> : <span className="text-text-secondary">—</span>}</span>
            </button>
          ))}
        </Card>
      )}
      {open && <ReportModal id={open} onClose={() => setOpen(null)} onChanged={reload} />}
    </>
  );
}

/** The goAML part of a Decision Room: its drafts, the reports prepared from it, and preparing one. */
export function GoamlRoomPanel({ roomId, decided }: { roomId: string; decided: boolean }) {
  const { data, reload } = useLoad(() => api.goamlRoom(roomId), [roomId, decided]);
  const [preparing, setPreparing] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  if (!data || (data.drafts.length === 0 && data.reports.length === 0)) return null;
  return (
    <Card className="p-[18px] mt-5 flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-[15px] font-semibold">goAML report</div>
          <div className="text-small text-text-secondary">For the FIU, from this room's decision. Nothing is sent: a person files it in goAML.</div>
        </div>
        {data.may_prepare ? <Button variant="primary" icon="file" onClick={() => setPreparing(true)}>Prepare goAML report</Button>
          : data.why && <span className="text-small text-text-secondary">{data.why}</span>}
      </div>
      {data.reports.map((r) => (
        <button key={r.id} type="button" onClick={() => setOpen(r.id)}
          className="flex items-center gap-3 px-3 py-2.5 border border-hairline rounded-control text-left text-[13px] hover:bg-container-low cursor-pointer">
          <Mono>{r.entity_reference}</Mono>
          <Chip tone={GOAML_STATUS[r.status].tone}>{GOAML_STATUS[r.status].label}</Chip>
          <span className="text-text-secondary truncate flex-1">from {r.output_name} · {r.reporter.first_name} {r.reporter.last_name}, {timeAgo(r.created_at)}</span>
          {r.filed && <span className="text-small">FIU ref <Mono>{r.filed.fiu_ref_number}</Mono></span>}
        </button>
      ))}
      {preparing && <PrepareModal room={data} onClose={() => setPreparing(false)}
        onPrepared={async (id) => { setPreparing(false); await reload(); setOpen(id); }} />}
      {open && <ReportModal id={open} onClose={() => setOpen(null)} onChanged={reload} />}
    </Card>
  );
}

function PrepareModal({ room, onClose, onPrepared }: { room: GoamlRoom; onClose: () => void; onPrepared: (id: string) => Promise<void> }) {
  const usable = room.drafts.filter((d) => d.problems.length === 0);
  const [output, setOutput] = useState(usable[0]?.output_id ?? room.drafts[0]?.output_id ?? "");
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [occupation, setOccupation] = useState("");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chosen = room.drafts.find((d) => d.output_id === output);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const r = await api.prepareGoaml({ room_id: room.room_id, output_id: output,
        reporter: { first_name: first.trim(), last_name: last.trim(), ...(occupation.trim() ? { occupation: occupation.trim() } : {}), ...(phone.trim() ? { phone: phone.trim() } : {}) } });
      await onPrepared(r.id);
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  }

  return (
    <Modal title="Prepare goAML report" subtitle="You are named as the reporting person; your email is your account." onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" type="submit" form="goaml-prepare" disabled={busy || !output || !first.trim() || !last.trim()}>{busy && <Spinner />}Prepare and check</Button></>}>
      <form id="goaml-prepare" onSubmit={submit}>
        <Field label="Draft" hint="The fleet's goAML draft in this room's evidence.">
          <select className={INPUT} value={output} onChange={(e) => setOutput(e.target.value)}>
            {room.drafts.map((d) => <option key={d.output_id} value={d.output_id}>
              {d.name} ({d.report_code ?? "?"}, {d.transactions ? `${d.transactions} transaction${d.transactions === 1 ? "" : "s"}` : `${d.parties} part${d.parties === 1 ? "y" : "ies"}`}){d.produced_by ? ` by ${d.produced_by}` : ""}</option>)}
          </select>
        </Field>
        {chosen && chosen.problems.length > 0 && (
          <Banner tone="warning" className="mb-4 text-small">This draft is incomplete; the report will fail until it is fixed:
            <ul className="m-0 mt-1 pl-4">{chosen.problems.slice(0, 6).map((p) => <li key={p}>{p}</li>)}</ul></Banner>
        )}
        <div className="grid grid-cols-2 gap-x-4">
          <Field label="First name"><input className={INPUT} value={first} maxLength={100} onChange={(e) => setFirst(e.target.value)} /></Field>
          <Field label="Last name"><input className={INPUT} value={last} maxLength={100} onChange={(e) => setLast(e.target.value)} /></Field>
          <Field label="Occupation" hint="e.g. MLRO"><input className={INPUT} value={occupation} maxLength={255} onChange={(e) => setOccupation(e.target.value)} /></Field>
          <Field label="Work phone"><input className={INPUT} value={phone} maxLength={50} onChange={(e) => setPhone(e.target.value)} /></Field>
        </div>
        {error && <Banner tone="error">{error}</Banner>}
      </form>
    </Modal>
  );
}

function ReportModal({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => Promise<void> | void }) {
  const { can } = useAuth();
  const { data: r, error, reload } = useLoad(() => api.goamlReport(id), [id]);
  const { data: settings } = useLoad(api.goamlSettings, []);
  const [ref, setRef] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [actError, setActError] = useState<string | null>(null);

  async function act(fn: () => Promise<GoamlReport>) {
    setBusy(true); setActError(null);
    try { await fn(); await reload(); await onChanged(); } catch (e) { setActError(errorText(e)); } finally { setBusy(false); }
  }

  const st = r ? GOAML_STATUS[r.status] : null;
  return (
    <Modal width={760} title={r ? <>goAML report <Mono>{r.entity_reference}</Mono></> : "goAML report"}
      subtitle={r ? <>{r.report_code} · case {r.case ?? "—"} · <Link to={`/rooms/${r.room_id}`}>the room</Link></> : undefined} onClose={onClose}
      footer={<>
        {r && <a href={goamlXmlUrl(r.id)} className="no-underline"><Button icon="download">{r.status === "ready" || r.status === "filed" ? "Download XML" : "Download (not valid)"}</Button></a>}
        {r && r.status !== "filed" && can("goaml.prepare") && <Button disabled={busy} onClick={() => void act(() => api.recheckGoaml(r.id))}>Check again</Button>}
        <Button variant="primary" onClick={onClose}>Close</Button>
      </>}>
      {error && <Banner tone="error">{error}</Banner>}
      {!r ? <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div> : (
        <div className="flex flex-col gap-4 text-[13px]">
          <div className="flex items-center gap-2 flex-wrap">
            <Chip tone={st!.tone}>{st!.label}</Chip>
            <span className="text-text-secondary">{st!.hint}</span>
          </div>
          {checkedAgainstOlderSchema(r, settings ?? null) && <Banner tone="warning">A newer FIU schema was loaded since this was checked: check it again.</Banner>}
          <div className="grid grid-cols-[160px_minmax(0,1fr)] gap-y-1.5">
            <span className="text-text-secondary">Reporting person</span><span>{r.reporter.first_name} {r.reporter.last_name} ({r.reporter.email})</span>
            <span className="text-text-secondary">Decision</span><span>{r.decision?.option?.label ?? "—"}{r.decision && r.decision.agreed === false ? " (the deciders disagreed)" : ""}</span>
            <span className="text-text-secondary">From</span><span>{r.output_name}</span>
            <span className="text-text-secondary">Checked against</span><span>{r.schema ? <>{r.schema.name} <Mono className="text-[11px]">{r.schema.sha256.slice(0, 12)}</Mono></> : "no FIU schema loaded"} · {formatDateTime(r.checked_at)}</span>
            {r.filed && <><span className="text-text-secondary">Filed</span><span>by {r.filed.by}, {formatDateTime(r.filed.at)} · FIU ref <Mono>{r.filed.fiu_ref_number}</Mono>{r.filed.note ? ` · ${r.filed.note}` : ""}</span></>}
          </div>
          {r.problems.length > 0 && <Issues title="Problems in the draft" items={r.problems} />}
          {r.errors.length > 0 && <Issues title="What the FIU's schema rejects" items={r.errors} />}
          {r.status === "ready" && can("goaml.prepare") && (
            <form onSubmit={(e) => { e.preventDefault(); void act(() => api.goamlFiled(r.id, { fiu_ref_number: ref.trim(), note: note.trim() })); }}
              className="border border-hairline rounded-control p-3 flex flex-col gap-2">
              <div className="font-semibold">After you upload it in goAML</div>
              <div className="grid grid-cols-[1fr_1fr_auto] gap-2 items-end">
                <Field label="FIU reference"><input className={INPUT} value={ref} maxLength={255} onChange={(e) => setRef(e.target.value)} placeholder="the reference goAML gave the report" /></Field>
                <Field label="Note (optional)"><input className={INPUT} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} /></Field>
                <div className="mb-4"><Button variant="primary" type="submit" disabled={busy || !ref.trim()}>Record as filed</Button></div>
              </div>
            </form>
          )}
          {actError && <Banner tone="error">{actError}</Banner>}
          <details>
            <summary className="cursor-pointer text-text-secondary">The XML</summary>
            <pre className="mt-2 mb-0 p-3 bg-container-low rounded-control font-mono text-[11px] max-h-[320px] overflow-auto whitespace-pre">{r.xml}</pre>
          </details>
        </div>
      )}
    </Modal>
  );
}

function Issues({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <div className="text-label uppercase text-text-secondary mb-1.5">{title}</div>
      <ul className="m-0 pl-4 flex flex-col gap-1">{items.map((e) => <li key={e} className="font-mono text-[12px] break-words">{e}</li>)}</ul>
    </div>
  );
}

/** Settings → goAML: the bank as a reporting entity, and the FIU's schema. */
export function GoamlSettingsPanel() {
  const { can } = useAuth();
  const { data, error, reload } = useLoad(api.goamlSettings, []);
  if (error) return <Banner tone="error">{error}</Banner>;
  if (!data) return <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div>;
  return (
    <div className="flex flex-col gap-5">
      <p className="m-0 text-text-secondary">
        goAML is how the bank files suspicious transaction reports with its FIU; in Egypt that is the EMLCU. Fleet Control prepares each report
        from a decided Decision Room and checks it against the FIU's own schema. It never files: a person uploads the report in goAML.
      </p>
      <ProfileForm profile={data.profile} canEdit={can("goaml.manage")} onSaved={reload} />
      <SchemaCard schema={data.schema} canEdit={can("goaml.manage")} onLoaded={reload} draftSchema={data.draft_schema} />
    </div>
  );
}

function ProfileForm({ profile, canEdit, onSaved }: { profile: GoamlProfile | null; canEdit: boolean; onSaved: () => Promise<void> }) {
  const [fiu, setFiu] = useState(profile?.fiu ?? "EMLCU (Egypt)");
  const [rentity, setRentity] = useState(profile?.rentity_id ? String(profile.rentity_id) : "");
  const [branch, setBranch] = useState(profile?.rentity_branch ?? "");
  const [currency, setCurrency] = useState(profile?.currency_code_local ?? "EGP");
  const [addrType, setAddrType] = useState(profile?.location?.address_type ?? "");
  const [address, setAddress] = useState(profile?.location?.address ?? "");
  const [city, setCity] = useState(profile?.location?.city ?? "");
  const [country, setCountry] = useState(profile?.location?.country_code ?? "EG");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setMsg(null);
    try {
      await api.saveGoamlProfile({ fiu: fiu.trim(), rentity_id: Number(rentity), rentity_branch: branch.trim() || null,
        currency_code_local: currency.trim().toUpperCase(), submission_code: "E",
        location: address.trim() ? { address_type: addrType.trim(), address: address.trim(), city: city.trim(), country_code: country.trim().toUpperCase() } : null });
      await onSaved();
      setMsg({ tone: "success", text: "Saved." });
    } catch (err) { setMsg({ tone: "error", text: errorText(err) }); } finally { setBusy(false); }
  }

  return (
    <Card className="p-5">
      <div className="text-[15px] font-semibold mb-3">The bank as a reporting entity</div>
      <form onSubmit={save} className="grid grid-cols-2 gap-x-4">
        <Field label="FIU"><input className={INPUT} disabled={!canEdit} value={fiu} onChange={(e) => setFiu(e.target.value)} /></Field>
        <Field label="Reporting-entity id" hint="The number the FIU assigned the bank in goAML."><input className={INPUT} disabled={!canEdit} inputMode="numeric" value={rentity} onChange={(e) => setRentity(e.target.value.replace(/\D/g, ""))} /></Field>
        <Field label="Branch (optional)"><input className={INPUT} disabled={!canEdit} value={branch} onChange={(e) => setBranch(e.target.value)} /></Field>
        <Field label="Local currency"><input className={INPUT} disabled={!canEdit} maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value)} /></Field>
        <Field label="Address type" hint="The FIU's code, from its code lists."><input className={INPUT} disabled={!canEdit} value={addrType} onChange={(e) => setAddrType(e.target.value)} /></Field>
        <Field label="Address"><input className={INPUT} disabled={!canEdit} value={address} onChange={(e) => setAddress(e.target.value)} /></Field>
        <Field label="City"><input className={INPUT} disabled={!canEdit} value={city} onChange={(e) => setCity(e.target.value)} /></Field>
        <Field label="Country code"><input className={INPUT} disabled={!canEdit} maxLength={2} value={country} onChange={(e) => setCountry(e.target.value)} /></Field>
        {canEdit && <div className="col-span-2 flex items-center gap-3"><Button variant="primary" type="submit" disabled={busy || !rentity}>{busy && <Spinner />}Save</Button>
          {msg && <span className={msg.tone === "error" ? "text-error text-small" : "text-success text-small"}>{msg.text}</span>}</div>}
      </form>
    </Card>
  );
}

function SchemaCard({ schema, canEdit, onLoaded, draftSchema }: {
  schema: GoamlSchemaInfo | null; canEdit: boolean; onLoaded: () => Promise<void>; draftSchema: string;
}) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  async function load(file: File) {
    setBusy(true); setMsg(null);
    try {
      await api.loadGoamlSchema(file.name, await file.text());
      await onLoaded();
      setMsg({ tone: "success", text: `${file.name} loaded. Reports are checked against it from now on; check older ones again.` });
    } catch (err) { setMsg({ tone: "error", text: errorText(err) }); } finally { setBusy(false); }
  }

  return (
    <Card className="p-5 flex flex-col gap-3">
      <div className="text-[15px] font-semibold">The FIU's goAML schema</div>
      {schema ? (
        <div className="text-[13px]">{schema.name} · <Mono className="text-[11px]">{schema.sha256.slice(0, 16)}</Mono> · loaded by {schema.loaded_by}, {formatDateTime(schema.loaded_at)}</div>
      ) : <Banner tone="warning">No schema loaded: reports can be prepared but not checked, so none can be filed.</Banner>}
      <p className="m-0 text-small text-text-secondary">
        Download the XSD from the FIU's goAML portal (goAML Web → XML schema) and load it here. Every FIU publishes its own: its code
        lists and rules decide whether a report is valid. Agents write drafts as <Mono>{draftSchema}</Mono>, using goAML's own element names.
      </p>
      {canEdit && (
        <label className="inline-flex items-center gap-3">
          <input type="file" accept=".xsd,application/xml,text/xml" disabled={busy} onChange={(e) => { const f = e.target.files?.[0]; if (f) void load(f); e.target.value = ""; }} />
          {busy && <Spinner />}
        </label>
      )}
      {msg && <Banner tone={msg.tone}>{msg.text}</Banner>}
    </Card>
  );
}

