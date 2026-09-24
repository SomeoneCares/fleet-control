import { Link } from "react-router";
import { api, type DecisionRoomRow } from "../api/client";
import { useLoad } from "../lib/hooks";
import { decisionQueue, dueLabel, statusChip, waitingLabel } from "../lib/rooms";
import { formatDateTime } from "../lib/view";
import { Banner, Card, Chip, Icon, PageHeader, Spinner } from "../components/ui";

// design/screens/WorkspaceHome: decisions waiting for me, and the ones I have already made.
/** Workflow runs stopped at a gate you may decide (your role, or escalated to you). */
function WorkflowGates() {
  const { data: gates } = useLoad(api.workflowGatesForMe, [], 20_000);
  if (!gates || gates.length === 0) return null;
  return (
    <Card className="p-[18px] flex flex-col gap-2.5">
      <div className="flex items-center justify-between">
        <span className="text-[15px] font-semibold">Workflow approvals waiting for me</span>
        <Chip tone="warning">{gates.length} waiting</Chip>
      </div>
      {gates.map((g) => (
        <Link key={g.id} to={`/workflow-runs/${g.id}`} className="flex items-center gap-3 py-2 border-b border-hairline last:border-b-0 no-underline text-text">
          <span className="flex-1 min-w-0">
            <span className="block text-[13px] font-medium truncate">{g.case || g.workflow_id}</span>
            <span className="text-small text-text-secondary">{g.workflow_id} · step {(g.gate?.index ?? 0) + 1} of {g.progress.total} · {g.blueprint}</span>
          </span>
          {g.gate?.escalated_at ? <Chip tone="error">Escalated to you</Chip> : g.overdue ? <Chip tone="error">Overdue</Chip> : null}
        </Link>
      ))}
    </Card>
  );
}

export function MyDecisionsScreen() {
  const { data: rooms, error } = useLoad(() => api.rooms(), [], 20_000);
  const { waiting, decided, elsewhere } = decisionQueue(rooms ?? []);

  return (
    <>
      <PageHeader crumb="Workspace" title="My decisions"
        subtitle="Rooms waiting for you, and the ones you have decided. Every decision keeps its rationale." />
      {error && <Banner tone="error" className="mb-4">{error}</Banner>}
      {!rooms && !error && <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div>}

      {rooms && (
        <div className="flex flex-col gap-5">
          <Card className="p-[18px] flex flex-col gap-2.5">
            <div className="flex items-center justify-between">
              <span className="text-[15px] font-semibold">Decisions waiting for me</span>
              <Chip tone={waiting.length ? "warning" : "success"}>{waiting.length ? `${waiting.length} waiting` : "Nothing waiting"}</Chip>
            </div>
            {waiting.length === 0 && <p className="m-0 text-text-secondary">No room needs your decision right now.</p>}
            <div className="flex flex-col">
              {waiting.map((r) => <RoomLine key={r.id} room={r} />)}
            </div>
          </Card>

          <WorkflowGates />

          {decided.length > 0 && (
            <Card className="p-[18px] flex flex-col gap-2.5">
              <span className="text-[15px] font-semibold">Decided by you</span>
              <div className="flex flex-col">
                {decided.map((r) => {
                  const chip = statusChip(r);
                  return (
                    <Link key={r.id} to={`/rooms/${r.id}`}
                      className="flex items-center gap-4 py-3.5 border-b border-hairline last:border-b-0 no-underline text-text hover:bg-surface">
                      <span className="flex-1 min-w-0 flex flex-col gap-0.5">
                        <span className="font-semibold truncate">{r.question}</span>
                        <span className="text-small text-text-secondary truncate">
                          {r.case ? `Case ${r.case} · ` : ""}
                          {r.outcome?.option ? `you chose ${r.outcome.option.label} · ` : ""}
                          {formatDateTime(r.updated_at)}
                        </span>
                      </span>
                      <Chip tone={chip.tone}>{chip.label}</Chip>
                      <Icon name="arrowRight" className="text-text-secondary shrink-0" />
                    </Link>
                  );
                })}
              </div>
            </Card>
          )}

          {elsewhere.length > 0 && (
            <Card className="px-[18px] py-3.5 text-[13px] text-text-secondary">
              {elsewhere.length} open room{elsewhere.length === 1 ? "" : "s"} you can read but not decide —
              they are waiting for someone else, or your role does not record decisions.{" "}
              <Link to="/rooms">See all rooms</Link>.
            </Card>
          )}
        </div>
      )}
    </>
  );
}

function RoomLine({ room }: { room: DecisionRoomRow }) {
  const due = dueLabel(room.due_at);
  const waiting = waitingLabel(room);
  return (
    <div className="flex items-center gap-4 py-3.5 border-b border-hairline last:border-b-0">
      <div className="flex-1 min-w-0 flex flex-col gap-0.5">
        <div className="font-semibold truncate">{room.question}</div>
        <div className="text-small text-text-secondary truncate">
          {room.case ? `Case ${room.case} · ` : ""}opened by {room.opened_by}
          {room.opened_by_kind === "agent" ? " (agent)" : ""}
          {waiting && room.second_approver ? " · second approval" : ""}
        </div>
      </div>
      {due && <Chip tone={due.tone}>{due.text}</Chip>}
      <Link to={`/rooms/${room.id}`}
        className="h-8 px-3.5 inline-flex items-center rounded-control border border-border bg-white text-[13px] font-medium no-underline text-text hover:bg-container-low shrink-0">
        Open
      </Link>
    </div>
  );
}
