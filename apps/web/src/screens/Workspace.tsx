import { Link } from "react-router";
import { api } from "../api/client";
import { useAuth, useMe } from "../lib/auth";
import { useLoad } from "../lib/hooks";
import { CLASSIFICATION_LABEL, CLASSIFICATION_TONE } from "../lib/content";
import { KIND_ICON, KIND_LABEL } from "../lib/outputs";
import { decisionQueue, dueLabel } from "../lib/rooms";
import { ENV_LABEL, approvalFor, formatDateTime, prettyId, timeAgo } from "../lib/view";
import { Banner, Card, Chip, Icon, PageHeader, Spinner } from "../components/ui";

const DAY = 86_400;

/** design/screens/WorkspaceHome: what is waiting for this person, and what the fleet has produced for them. */
export function WorkspaceHome() {
  const me = useMe();
  const { can } = useAuth();
  const { data: plans, error } = useLoad(() => api.plans("planned"), [], 15_000);
  const { data: rooms } = useLoad(() => (can("rooms.read") ? api.rooms() : Promise.resolve([])), [], 20_000);
  const { data: outputs } = useLoad(() => (can("content.read") ? api.outputs({ limit: 6 }) : Promise.resolve([])), [], 20_000);
  const { waiting } = decisionQueue(rooms ?? []);
  const mine = (plans ?? []).filter((p) => approvalFor(p, me).can);
  const others = (plans ?? []).filter((p) => !approvalFor(p, me).can && p.approvals.length < p.approvals_required);
  const fresh = (outputs ?? []).filter((o) => o.at > Date.now() / 1000 - DAY).length;

  return (
    <>
      <PageHeader crumb="Workspace" title={`Hello, ${me.name.split(" ")[0]}`}
        subtitle={rooms
          ? `${waiting.length === 0 ? "No decision is" : waiting.length === 1 ? "1 decision is" : `${waiting.length} decisions are`} waiting for you.${fresh > 0 ? ` The fleet produced ${fresh} output${fresh === 1 ? "" : "s"} in the last day.` : ""}`
          : `Signed in as ${me.role_label}.`} />
      {error && <Banner tone="error" className="mb-4">{error}</Banner>}

      <div className="grid grid-cols-[minmax(0,1fr)_340px] gap-5 items-start">
        <div className="flex flex-col gap-5 min-w-0">
          {can("rooms.read") && (
            <Card className="p-[18px] flex flex-col gap-2.5">
              <div className="flex items-center justify-between">
                <h2 className="text-section m-0">Decisions waiting for me</h2>
                {rooms && <Chip tone={waiting.length ? "warning" : "success"}>{waiting.length ? `${waiting.length} waiting` : "Nothing waiting"}</Chip>}
              </div>
              {!rooms && <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading rooms…</div>}
              {rooms && waiting.length === 0 && <p className="m-0 text-text-secondary">No decision room needs you right now.</p>}
              <div className="flex flex-col">
                {waiting.slice(0, 5).map((r) => {
                  const due = dueLabel(r.due_at);
                  return (
                    <Link key={r.id} to={`/rooms/${r.id}`}
                      className="flex items-center gap-4 py-3.5 border-b border-hairline last:border-b-0 no-underline text-text hover:bg-surface">
                      <span className="flex-1 min-w-0 flex flex-col gap-0.5">
                        <span className="font-semibold truncate">{r.question}</span>
                        <span className="text-small text-text-secondary truncate">
                          {r.case ? `Case ${r.case} · ` : ""}opened by {r.opened_by}{r.opened_by_kind === "agent" ? " (agent)" : ""}
                          {r.second_approver ? " · second approval" : ""}
                        </span>
                      </span>
                      {due && <Chip tone={due.tone}>{due.text}</Chip>}
                      <Icon name="arrowRight" className="text-text-secondary shrink-0" />
                    </Link>
                  );
                })}
              </div>
              {waiting.length > 5 && <Link to="/my-decisions" className="text-[13px]">See all {waiting.length}</Link>}
            </Card>
          )}

          <Card className="overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-hairline">
              <h2 className="text-section m-0">Waiting for your approval</h2>
              {plans && <Chip tone={mine.length ? "warning" : "success"}>{mine.length ? `${mine.length} to review` : "Nothing waiting"}</Chip>}
            </div>
            {!plans && <div className="px-5 py-4 flex gap-2 items-center text-text-secondary"><Spinner /> Loading plans…</div>}
            {plans && mine.length === 0 && <p className="m-0 px-5 py-6 text-text-secondary">No plan needs your approval right now.</p>}
            {mine.map((p) => (
              <Link key={p.id} to={`/plans/${p.id}`} className="flex items-center gap-4 px-5 py-3.5 border-t border-hairline first:border-t-0 no-underline text-text hover:bg-surface">
                <Icon name="layers" className="text-secondary" />
                <div className="flex-1 min-w-0">
                  <div className="font-semibold">{prettyId(p.blueprint.name)} v{p.blueprint.version} → {p.target_instance}</div>
                  <div className="text-small text-text-secondary">{p.changes} change{p.changes === 1 ? "" : "s"} · planned by {p.created_by} · {formatDateTime(p.created_at)}</div>
                </div>
                <Chip tone="warning">{ENV_LABEL[p.environment]} · {p.approvals.length} of {p.approvals_required}</Chip>
                <Icon name="arrowRight" className="text-text-secondary" />
              </Link>
            ))}
            {others.length > 0 && (
              <div className="px-5 py-3 border-t border-hairline bg-surface text-small text-text-secondary">
                {others.length} more plan{others.length === 1 ? "" : "s"} waiting for someone else's approval.
              </div>
            )}
          </Card>

          {can("content.read") && (
            <Card className="p-[18px] flex flex-col gap-2.5">
              <div className="flex items-center justify-between">
                <h2 className="text-section m-0">Recent fleet outputs</h2>
                <Link to="/outputs" className="text-[13px]">See all</Link>
              </div>
              {!outputs && <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading outputs…</div>}
              {outputs?.length === 0 && <p className="m-0 text-text-secondary">The fleet has not shared an output with your role yet.</p>}
              <div className="flex flex-col">
                {outputs?.map((o) => (
                  <Link key={o.id} to="/outputs" className="flex items-center gap-3 py-2.5 border-b border-hairline last:border-b-0 no-underline text-text hover:bg-surface">
                    <Icon name={KIND_ICON[o.kind]} className="text-text-secondary shrink-0" />
                    <span className="flex-1 min-w-0 flex flex-col gap-0.5">
                      <span className="text-[13px] font-medium truncate">{o.name}</span>
                      <span className="text-small text-text-secondary truncate">{KIND_LABEL[o.kind]} · produced by {o.produced_by} · {timeAgo(o.at)}</span>
                    </span>
                    <Chip tone={CLASSIFICATION_TONE[o.classification]}>{CLASSIFICATION_LABEL[o.classification]}</Chip>
                  </Link>
                ))}
              </div>
            </Card>
          )}
        </div>

        <Card className="p-5">
          <h2 className="text-section m-0 mb-2">What you can do here</h2>
          <ul className="m-0 pl-5 text-[13px] flex flex-col gap-1.5">
            {can("rooms.decide") && <li>Decide in a <Link to="/rooms">Decision Room</Link> and record a rationale. Decisions are made here, never in a chat reply.</li>}
            {can("content.read") && <li>Read <Link to="/outputs">outputs</Link> the fleet is allowed to share with your role.</li>}
            {can("plans.approve.production") && <li>Approve production plans.</li>}
            {can("ask.use") && <li><Link to="/ask">Ask the fleet</Link> a question: it answers only from content your role may see, and shows its sources.</li>}
            {can("audit.read") && <li>Read the <Link to="/audit">audit log</Link>.</li>}
            <li>Read any plan you are sent a link to.</li>
          </ul>
        </Card>
      </div>
    </>
  );
}
