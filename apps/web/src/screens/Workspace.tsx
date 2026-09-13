import { Link } from "react-router";
import { api } from "../api/client";
import { useAuth, useMe } from "../lib/auth";
import { useLoad } from "../lib/hooks";
import { ENV_LABEL, approvalFor, formatDateTime, prettyId } from "../lib/view";
import { Banner, Card, Chip, Icon, PageHeader, Spinner } from "../components/ui";

/** Home for Approvers and Viewers until the Workspace (Decision Rooms, outputs, Ask the fleet) arrives in Slice 4. */
export function WorkspaceHome() {
  const me = useMe();
  const { can } = useAuth();
  const { data: plans, error } = useLoad(() => api.plans("planned"), [], 15_000);
  const mine = (plans ?? []).filter((p) => approvalFor(p, me).can);
  const others = (plans ?? []).filter((p) => !approvalFor(p, me).can && p.approvals.length < p.approvals_required);

  return (
    <>
      <PageHeader crumb="Workspace" title={`Hello, ${me.name.split(" ")[0]}`}
        subtitle={`Signed in as ${me.role_label}. Decision Rooms, fleet outputs and Ask the fleet arrive here in Slice 4.`} />
      {error && <Banner tone="error" className="mb-4">{error}</Banner>}
      <div className="grid grid-cols-[minmax(0,1fr)_340px] gap-5 items-start">
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
        <Card className="p-5">
          <h2 className="text-section m-0 mb-2">What you can do here</h2>
          <ul className="m-0 pl-5 text-[13px] flex flex-col gap-1.5">
            {can("plans.approve.production") && <li>Approve production plans. Approvals are recorded here, never from chat replies.</li>}
            {can("audit.read") && <li>Read the <Link to="/audit">audit log</Link>.</li>}
            <li>Read any plan you are sent a link to.</li>
          </ul>
        </Card>
      </div>
    </>
  );
}
