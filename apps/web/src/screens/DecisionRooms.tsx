import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, type ContentZone, type DecisionRoom, type EvidenceKind, type RoomStatus, type Verdict } from "../api/client";
import { useAuth, useMe } from "../lib/auth";
import { errorText, useLoad } from "../lib/hooks";
import {
  EVIDENCE_ICON, EVIDENCE_LABEL, casesOf, dueLabel, matchesQuery, optionLabel, statusChip, waitingLabel,
} from "../lib/rooms";
import { VERDICT_TONE } from "../lib/testlab";
import { formatDateTime, timeAgo } from "../lib/view";
import { Banner, Button, Card, Chip, Field, INPUT, Icon, Modal, Mono, PageHeader, Spinner, TEXTAREA } from "../components/ui";

const VERDICTS = Object.keys(VERDICT_TONE) as Verdict[];
const EVIDENCE_KINDS: EvidenceKind[] = ["file", "output", "claim", "note"];

// design/screens/DecisionRoom: the list of rooms this person may see.
export function DecisionRoomsScreen() {
  const { can } = useAuth();
  const { data: rooms, error, reload } = useLoad(() => api.rooms(), [], 20_000);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<RoomStatus | "">("");
  const [caseId, setCaseId] = useState("");
  const [opening, setOpening] = useState(false);
  const all = rooms ?? [];
  const rows = all.filter((r) => (!status || r.status === status) && (!caseId || r.case === caseId) && matchesQuery(r, query));

  return (
    <>
      <PageHeader crumb="Operate" title="Decision Rooms"
        subtitle="One question, the evidence behind it, and who decided. The room's content zone decides who may see it."
        actions={can("rooms.open") && <Button variant="primary" icon="plus" onClick={() => setOpening(true)}>Open a room</Button>} />
      {error && <Banner tone="error" className="mb-4">{error}</Banner>}

      <Card className="overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-hairline">
          <input className={`${INPUT} h-8 max-w-[280px]`} placeholder="Search rooms" value={query} onChange={(e) => setQuery(e.target.value)} />
          <select aria-label="Status" className={`${INPUT} h-8 w-[170px]`} value={status} onChange={(e) => setStatus(e.target.value as RoomStatus | "")}>
            <option value="">Status: All</option>
            <option value="open">Open</option>
            <option value="decided">Decided</option>
            <option value="cancelled">Cancelled</option>
          </select>
          <select aria-label="Case" className={`${INPUT} h-8 w-[190px]`} value={caseId} onChange={(e) => setCaseId(e.target.value)}>
            <option value="">Case: All</option>
            {casesOf(all).map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        {!rooms && !error && <div className="px-4 py-4 flex gap-2 items-center text-text-secondary"><Spinner /> Loading rooms…</div>}
        {rooms && rows.length === 0 && (
          <p className="m-0 px-4 py-6 text-[13px] text-text-secondary">
            {all.length === 0
              ? <>No decision room yet. A room is opened here, or by an agent working a case. What you can see depends on the room's <Link to="/content">content zone</Link>.</>
              : "No room matches the filters."}
          </p>
        )}
        {rows.map((r) => {
          const chip = statusChip(r);
          const due = dueLabel(r.due_at);
          const waiting = waitingLabel(r);
          return (
            <Link key={r.id} to={`/rooms/${r.id}`}
              className="flex items-center gap-4 px-4 py-3.5 border-b border-hairline no-underline text-text hover:bg-surface">
              <Icon name="chat" className="text-secondary shrink-0" />
              <span className="flex-1 min-w-0 flex flex-col gap-0.5">
                <span className="font-semibold truncate">{r.question}</span>
                <span className="text-small text-text-secondary truncate">
                  {r.case ? `Case ${r.case} · ` : ""}opened by {r.opened_by}{r.opened_by_kind === "agent" ? " (agent)" : ""} · {formatDateTime(r.created_at)}
                  {waiting ? ` · ${waiting}` : ""}
                </span>
              </span>
              <span className="text-small text-text-secondary shrink-0 hidden lg:block">
                {r.evidence} evidence item{r.evidence === 1 ? "" : "s"} · {r.findings} finding{r.findings === 1 ? "" : "s"} · {r.decisions} decision{r.decisions === 1 ? "" : "s"}
              </span>
              {r.mine && <Chip tone="neutral">You decided</Chip>}
              {due && r.status === "open" && <Chip tone={due.tone}>{due.text}</Chip>}
              <Chip tone={chip.tone}>{chip.label}</Chip>
              <Icon name="arrowRight" className="text-text-secondary shrink-0" />
            </Link>
          );
        })}
        {rows.length > 0 && (
          <div className="px-4 py-2.5 text-small text-text-secondary">Showing {rows.length} of {all.length} · newest first</div>
        )}
      </Card>

      {opening && <OpenRoomModal onClose={() => setOpening(false)} onOpened={async () => { setOpening(false); await reload(); }} />}
    </>
  );
}

// design/screens/DecisionRoom: evidence · what the fleet found · decision.
export function DecisionRoomScreen() {
  const { id = "" } = useParams();
  const me = useMe();
  const { can } = useAuth();
  const { data: room, error, reload } = useLoad(() => api.room(id), [id], 15_000);
  const [adding, setAdding] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  if (error) return <><PageHeader crumb={<Link to="/rooms">Decision Rooms</Link>} title="Decision room" /><Banner tone="error">{error}</Banner></>;
  if (!room) return <><PageHeader crumb={<Link to="/rooms">Decision Rooms</Link>} title="Decision room" /><div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div></>;

  const chip = statusChip(room);
  const due = dueLabel(room.due_at);
  const mayCancel = room.status === "open" && (room.opened_by === me.email || me.role === "admin");

  return (
    <>
      <PageHeader crumb={<Link to="/rooms">Decision Rooms</Link>} title={room.question}
        subtitle={<span className="flex items-center gap-2 flex-wrap">
          {room.case && <>Case <Mono>{room.case}</Mono> ·</>}
          <span>Opened {formatDateTime(room.created_at)} by {room.opened_by}{room.opened_by_kind === "agent" ? " (agent)" : ""}</span>
          <Chip tone={chip.tone}>{chip.label}</Chip>
          {due && room.status === "open" && <Chip tone={due.tone}>{due.text}</Chip>}
        </span>}
        actions={mayCancel && (
          <Button variant="danger" onClick={() => {
            if (!window.confirm("Cancel this room? Nothing is decided and it stays on the record.")) return;
            void api.cancelRoom(room.id).then(() => reload()).then(() => setMsg("Room cancelled."));
          }}>Cancel room</Button>
        )} />
      {msg && <Banner tone="info" className="mb-4">{msg}</Banner>}

      <div className="flex gap-5 items-start">
        <Card className="w-[300px] shrink-0 p-[18px] flex flex-col gap-3.5">
          <div className="flex items-center justify-between">
            <span className="text-[15px] font-semibold">Evidence</span>
            {can("rooms.open") && room.status === "open" && <Button onClick={() => setAdding(true)}>Add</Button>}
          </div>
          {room.evidence.length === 0 && <p className="m-0 text-small text-text-secondary">Nothing filed yet. Evidence is a file, a fleet output, an assurance claim, or a note.</p>}
          <div className="flex flex-col">
            {room.evidence.map((e, i) => (
              <div key={i} className="flex gap-2.5 items-start py-3 border-b border-hairline last:border-b-0">
                <Icon name={EVIDENCE_ICON[e.kind]} className="text-text-secondary shrink-0 mt-0.5" />
                <div className="flex flex-col gap-1.5 min-w-0">
                  <div className="text-[13px] font-medium leading-[18px]">{e.label}</div>
                  <div className="text-small text-text-secondary">{e.source || EVIDENCE_LABEL[e.kind]} · added by {e.added_by}</div>
                  {e.verdict && <div><Chip tone={VERDICT_TONE[e.verdict]}>{e.verdict}</Chip></div>}
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card className="flex-1 min-w-0 p-[18px] flex flex-col gap-3.5">
          <div className="text-[15px] font-semibold">What the fleet found</div>
          {room.findings.length === 0 && (
            <p className="m-0 text-small text-text-secondary">
              No agent has filed a finding yet. Agents post findings through the agent route; they never decide.
            </p>
          )}
          <div className="flex flex-col">
            {room.findings.map((f, i) => (
              <div key={i} className="flex gap-3.5 py-4 border-b border-hairline last:border-b-0">
                <div className="w-[140px] shrink-0 flex flex-col gap-0.5">
                  <div className="text-[13px] font-semibold text-secondary">{f.agent}</div>
                  <div className="text-small text-text-secondary">{timeAgo(f.at)}</div>
                  {f.run_id && <div className="text-small text-text-secondary"><Mono className="text-[11px]">{f.run_id}</Mono></div>}
                </div>
                <div className="flex-1 min-w-0 flex flex-col gap-2.5">
                  <div className="leading-[22px] whitespace-pre-wrap break-words">{f.text}</div>
                  {f.verdict && <div><Chip tone={VERDICT_TONE[f.verdict]}>{f.verdict}</Chip></div>}
                </div>
              </div>
            ))}
          </div>
        </Card>

        <DecisionRail room={room} onDecided={async () => { await reload(); setMsg("Your decision is recorded."); }} />
      </div>

      {adding && <EvidenceModal room={room} onClose={() => setAdding(false)} onAdded={async () => { setAdding(false); await reload(); }} />}
    </>
  );
}

function DecisionRail({ room, onDecided }: { room: DecisionRoom; onDecided: () => Promise<void> }) {
  const [option, setOption] = useState(room.options[0]?.id ?? "");
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const short = rationale.trim().length < 10;

  async function submit() {
    setBusy(true);
    setFailure(null);
    try {
      await api.decideRoom(room.id, { option, rationale: rationale.trim() });
      setRationale("");
      await onDecided();
    } catch (e) {
      setFailure(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="w-[320px] shrink-0 p-[18px] flex flex-col gap-3.5">
      <div className="text-[15px] font-semibold">Decision</div>

      {room.may_decide ? (
        <>
          <div className="flex flex-col gap-2">
            {room.options.map((o) => (
              <label key={o.id}
                className={`flex items-center gap-2.5 px-3 py-2.5 rounded-control border cursor-pointer text-[13px] ${option === o.id ? "border-primary bg-primary-tint font-medium" : "border-hairline bg-white"}`}>
                <input type="radio" name="room-option" className="accent-primary" checked={option === o.id} onChange={() => setOption(o.id)} />
                {o.label}
              </label>
            ))}
          </div>
          <Field label="Rationale" hint={short ? "At least 10 characters: the room keeps it on the record." : undefined}>
            <textarea className={`${TEXTAREA} min-h-[88px]`} value={rationale} onChange={(e) => setRationale(e.target.value)}
              placeholder="Why this option, given the evidence." />
          </Field>
          <Button variant="primary" disabled={busy || short || !option} onClick={() => void submit()}>{busy && <Spinner />}Record my decision</Button>
        </>
      ) : (
        <Banner tone={room.status === "open" ? "info" : "success"}>{room.reason ?? "This room is closed."}</Banner>
      )}

      {/* Whoever is looking — including the person who has just decided — an open room says who it waits for. */}
      {room.second_approver && room.status === "open" && (
        <div className="flex items-start gap-2 text-small text-text-secondary">
          <Icon name="lock" size={16} className="mt-0.5" />
          <span>
            Second approver: {room.second_approver}
            {room.decisions.some((d) => d.by === room.second_approver) ? "" : " · not yet decided"}. The room stays open
            until they have decided.
          </span>
        </div>
      )}

      {failure && <Banner tone="error">{failure}</Banner>}

      {room.decisions.length > 0 && (
        <div className="flex flex-col gap-2">
          <div className="text-label uppercase text-text-secondary">Recorded</div>
          <div className="border border-hairline rounded-control divide-y divide-hairline">
            {room.decisions.map((d, i) => (
              <div key={i} className="px-3 py-2.5 flex flex-col gap-1 text-[13px]">
                <div className="font-medium">{optionLabel(room, d.option)}</div>
                <div className="text-small text-text-secondary">{d.by} · {formatDateTime(d.at)}</div>
                <div className="leading-[18px] whitespace-pre-wrap break-words">{d.rationale}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {room.outcome && !room.outcome.agreed && (
        <Banner tone="warning">The deciders chose differently. The room records both rationales rather than picking one.</Banner>
      )}
    </Card>
  );
}

function EvidenceModal({ room, onClose, onAdded }: { room: DecisionRoom; onClose: () => void; onAdded: () => Promise<void> }) {
  const [kind, setKind] = useState<EvidenceKind>("note");
  const [label, setLabel] = useState("");
  const [ref, setRef] = useState("");
  const [source, setSource] = useState("");
  const [verdict, setVerdict] = useState<Verdict | "">("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const needsRef = kind !== "note";

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.addEvidence(room.id, {
        kind, label: label.trim(), ref: ref.trim() || undefined, source: source.trim() || undefined,
        verdict: verdict || undefined,
      });
      await onAdded();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <Modal width={560} title="Add evidence" subtitle="What the decision rests on. A file or output must be one you may already read." onClose={onClose}
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="evidence-form" disabled={busy || !label.trim() || (needsRef && !ref.trim())}>
          {busy && <Spinner />}Add evidence
        </Button>
      </>}>
      <form id="evidence-form" onSubmit={(e) => void submit(e)}>
        <Field label="Kind">
          <select className={INPUT} value={kind} onChange={(e) => setKind(e.target.value as EvidenceKind)}>
            {EVIDENCE_KINDS.map((k) => <option key={k} value={k}>{EVIDENCE_LABEL[k]}</option>)}
          </select>
        </Field>
        <Field label="Label">
          <input className={INPUT} autoFocus value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Board minutes, 28 Sep" />
        </Field>
        {needsRef && (
          <Field label="Reference" hint={kind === "claim" ? "The assurance claim's id." : `The ${kind}'s id, from ${kind === "file" ? "Content" : "Fleet outputs"}.`}>
            <input className={INPUT} value={ref} onChange={(e) => setRef(e.target.value)} placeholder={kind === "output" ? "out_…" : "file_…"} />
          </Field>
        )}
        <Field label="Source" hint="Where it came from, in words. Optional.">
          <input className={INPUT} value={source} onChange={(e) => setSource(e.target.value)} placeholder="Corporate registry MCP" />
        </Field>
        <Field label="Verdict" hint="Only if assurance has judged it.">
          <select className={INPUT} value={verdict} onChange={(e) => setVerdict(e.target.value as Verdict | "")}>
            <option value="">No verdict</option>
            {VERDICTS.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </Field>
        {error && <Banner tone="error">{error}</Banner>}
      </form>
    </Modal>
  );
}

function OpenRoomModal({ onClose, onOpened }: { onClose: () => void; onOpened: () => Promise<void> }) {
  const navigate = useNavigate();
  const { data: zones } = useLoad(api.contentZones, []);
  const readable = (zones ?? []).filter((z: ContentZone) => z.may_read);
  const [question, setQuestion] = useState("");
  const [zone, setZone] = useState("");
  const [options, setOptions] = useState("");
  const [caseId, setCaseId] = useState("");
  const [due, setDue] = useState("");
  const [second, setSecond] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const optionList = options.split("\n").map((o) => o.trim()).filter(Boolean);
  const chosenZone = zone || readable[0]?.id || "";
  const ready = question.trim().length >= 10 && chosenZone && optionList.length >= 2 && optionList.length <= 6;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const room = await api.openRoom({
        question: question.trim(), zone: chosenZone, options: optionList,
        case: caseId.trim() || undefined,
        due_at: due ? Date.parse(due + "T17:00:00") / 1000 : undefined,
        second_approver: second.trim() || undefined,
      });
      await onOpened();
      navigate(`/rooms/${room.id}`);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <Modal width={620} title="Open a decision room" onClose={onClose}
      subtitle="One question, two to six options. The content zone decides who may see the room."
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="room-form" disabled={busy || !ready}>{busy && <Spinner />}Open room</Button>
      </>}>
      <form id="room-form" onSubmit={(e) => void submit(e)}>
        <Field label="Question" hint="Between 10 and 300 characters.">
          <input className={INPUT} autoFocus value={question} onChange={(e) => setQuestion(e.target.value)}
            placeholder="Should we file a SAR for Alpha Trading Ltd?" />
        </Field>
        <Field label="Content zone" hint="Only zones you may read.">
          <select className={INPUT} value={chosenZone} onChange={(e) => setZone(e.target.value)}>
            {readable.length === 0 && <option value="">No zone you may read</option>}
            {readable.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
          </select>
        </Field>
        <Field label="Options" hint="One per line, two to six.">
          <textarea className={`${TEXTAREA} min-h-[110px]`} value={options} onChange={(e) => setOptions(e.target.value)}
            placeholder={"File SAR draft for signature\nRequest more evidence\nClose as legitimate transfer"} />
        </Field>
        <Field label="Case" hint="Optional.">
          <input className={INPUT} value={caseId} onChange={(e) => setCaseId(e.target.value)} placeholder="AML-2026-0412" />
        </Field>
        <Field label="Decision due" hint="Optional.">
          <input className={INPUT} type="date" value={due} onChange={(e) => setDue(e.target.value)} />
        </Field>
        <Field label="Second approver" hint="Optional. The room stays open until this person has decided too.">
          <input className={INPUT} value={second} onChange={(e) => setSecond(e.target.value)} placeholder="marcus.okafor@fleetcontrol.local" />
        </Field>
        {error && <Banner tone="error">{error}</Banner>}
      </form>
    </Modal>
  );
}
