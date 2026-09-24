import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { api, type Plan, type PlanRow } from "../api/client";
import { useMe } from "../lib/auth";
import { errorText, useLoad } from "../lib/hooks";
import { ENV_LABEL, applyLabel, approvalFor, canApply, changeCounts, planPhase, type Tone } from "../lib/view";
import { Banner, Button, Card, Chip, Icon, Mono, PageHeader, Spinner, TONE_CLASS, type IconName } from "../components/ui";

const ROW_TONE: Record<PlanRow["kind"], Tone> = { create: "success", update: "info", remove: "error", approval: "warning" };
const VERB: Record<PlanRow["kind"], string> = { create: "create", update: "update", remove: "remove", approval: "" };

export function ApplyPlanScreen() {
  const { id = "" } = useParams();
  const [pollMs, setPollMs] = useState(0);
  const { data: plan, error, reload } = useLoad(() => api.plan(id), [id], pollMs);
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const me = useMe();

  useEffect(() => setPollMs(plan?.status === "applying" ? 1500 : 0), [plan?.status]);

  async function act(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setFailure(null);
    try {
      await fn();
      await reload();
    } catch (e) {
      setFailure(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  if (error && !plan) return <Banner tone="error">{error}</Banner>;
  if (!plan) return <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading plan…</div>;

  const phase = planPhase(plan);
  const approval = approvalFor(plan, me);
  const mayApply = canApply(plan, me);
  const counts = changeCounts(plan);
  const actionable = plan.changes.filter((r) => r.kind !== "approval").length;

  return (
    <>
      <PageHeader
        crumb={me.portal === "workspace"
          ? <><Link to="/workspace">Workspace</Link> › Plan</>
          : <><Link to="/blueprints">Library</Link> › <Link to={`/blueprints?name=${plan.blueprint.name}`}>{plan.blueprint.name}</Link></>}
        title={<>Plan: apply blueprint v{plan.blueprint.version} to {plan.target_instance}</>}
        subtitle={`Review every change before anything touches Hermes.${plan.approvals_required ? ` Applying to ${ENV_LABEL[plan.environment].toLowerCase()} needs ${plan.approvals_required} approvals.` : ""}`}
        actions={<>
          {phase === "needs-approval" && approval.can && (
            <Button icon="check" disabled={busy !== null} onClick={() => void act("approve", () => api.approvePlan(plan.id))}>
              {busy === "approve" && <Spinner />}Approve ({plan.approvals.length}/{plan.approvals_required})
            </Button>
          )}
          <Button variant="primary" icon="check" disabled={phase !== "ready" || busy !== null || !mayApply}
            title={mayApply ? undefined : `The ${me.role_label} role cannot apply ${plan.environment} plans`}
            onClick={() => void act("apply", () => api.applyPlan(plan.id))}>
            {busy === "apply" && <Spinner />}{phase === "applied" ? "Applied" : phase === "applying" ? "Applying…" : applyLabel(plan.environment)}
          </Button>
        </>} />
      {failure && <Banner tone="error" className="mb-4">{failure}</Banner>}
      {phase === "needs-approval" && approval.reason && <Banner tone="info" className="mb-4">{approval.reason}</Banner>}
      {phase === "ready" && !mayApply && <Banner tone="info" className="mb-4">Approved. An {plan.environment === "production" ? "Admin or Operator" : "Admin, Fleet Architect or Operator"} applies it.</Banner>}

      <div className="grid grid-cols-[1fr_360px] gap-5 items-start">
        <Card className="overflow-hidden">
          <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-hairline">
            <h2 className="text-section m-0">Changes · {actionable}</h2>
            <div className="flex gap-1.5">
              {counts.create > 0 && <Chip tone="success">{counts.create} create</Chip>}
              {counts.update > 0 && <Chip tone="info">{counts.update} update</Chip>}
              {counts.remove > 0 && <Chip tone="error">{counts.remove} remove</Chip>}
              {counts.approval > 0 && <Chip tone="warning">{counts.approval} needs approval</Chip>}
            </div>
          </div>
          {plan.changes.length === 0 && <div className="px-5 py-6 text-text-secondary">Nothing to change: the instance already matches this version.</div>}
          {plan.changes.map((r, n) => <ChangeRow key={`${r.object}-${n}`} row={r} />)}
          {(plan.no_change.profiles.length > 0 || plan.unmanaged_profiles.length > 0) && (
            <div className="px-5 py-3.5 border-t border-hairline bg-surface text-small text-text-secondary flex flex-col gap-1">
              {plan.no_change.profiles.length > 0 && <div>No changes: {plan.no_change.profiles.join(", ")}</div>}
              {plan.unmanaged_profiles.length > 0 && <div>Not managed by this blueprint, left untouched: {plan.unmanaged_profiles.join(", ")}</div>}
            </div>
          )}
        </Card>

        <div className="flex flex-col gap-4">
          <Preflight plan={plan} />
          {plan.status === "planned" ? <WhatHappens /> : <ApplyOutcome plan={plan} />}
        </div>
      </div>
    </>
  );
}

function ChangeRow({ row }: { row: PlanRow }) {
  const [type, ...rest] = row.object.split(" ");
  return (
    <div className="flex items-start gap-3 px-5 py-3.5 border-t border-hairline first:border-t-0">
      <span className={`size-6 shrink-0 rounded-control flex items-center justify-center font-mono font-bold ${TONE_CLASS[ROW_TONE[row.kind]]}`}>{row.symbol === "-" ? "−" : row.symbol}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span>{VERB[row.kind] ? `${VERB[row.kind]} ${type}` : type} <strong>{rest.join(" ")}</strong></span>
          {row.kind === "approval" && <Chip tone="warning">Requires human approval</Chip>}
        </div>
        <div className="text-small text-text-secondary mt-0.5 break-words">{row.description}</div>
      </div>
      <Mono className="text-text-secondary shrink-0">via {row.method}</Mono>
    </div>
  );
}

function Check({ icon, tone, title, detail }: { icon: IconName; tone: Tone; title: string; detail: string }) {
  const color = tone === "success" ? "text-success" : tone === "warning" ? "text-warning" : tone === "error" ? "text-error" : "text-text-secondary";
  return (
    <div className="flex gap-3 px-4 py-3 border-t border-hairline first:border-t-0">
      <Icon name={icon} size={18} className={`${color} mt-0.5`} />
      <div>
        <div className="font-medium">{title}</div>
        <div className={`text-small ${tone === "warning" || tone === "error" ? color : "text-text-secondary"}`}>{detail}</div>
      </div>
    </div>
  );
}

function Preflight({ plan }: { plan: Plan }) {
  const phase = planPhase(plan);
  const badge: Record<typeof phase, [Tone, string]> = {
    ready: ["success", `Ready for ${ENV_LABEL[plan.environment].toLowerCase()}`], "needs-approval": ["warning", "Needs approval"],
    blocked: ["error", "Blocked"], "no-changes": ["neutral", "Nothing to apply"], applying: ["info", "Applying…"],
    applied: ["success", "Applied"], failed: ["error", "Failed"],
  };
  const [tone, label] = badge[phase];
  const policyProfiles = Object.values(plan.policy_push ?? {});
  const deny = policyProfiles.reduce((n, p) => n + p.deny_tools.length, 0);
  const approve = policyProfiles.reduce((n, p) => n + p.approve_tools.length, 0);
  return (
    <Card>
      <div className="flex items-center justify-between px-4 py-3.5 border-b border-hairline">
        <h2 className="text-section m-0">Pre-flight</h2>
        <Chip tone={tone}>{label}</Chip>
      </div>
      <Check icon={plan.can_apply ? "checkCircle" : "xCircle"} tone={plan.can_apply ? "success" : "error"} title="Write path"
        detail={plan.can_apply ? `Fleet Control Agent on ${plan.target_instance}` : plan.blocked_reason ?? "Cannot apply"} />
      <Check icon={plan.approvals.length >= plan.approvals_required ? "checkCircle" : "clock"}
        tone={plan.approvals.length >= plan.approvals_required ? "success" : "warning"} title="Approvals"
        detail={plan.approvals_required ? `${plan.approvals.length} of ${plan.approvals_required} for ${ENV_LABEL[plan.environment].toLowerCase()}${plan.approvals.length ? ` (${plan.approvals.join(", ")})` : ""}` : `Not needed for ${ENV_LABEL[plan.environment].toLowerCase()}`} />
      <Check icon="shield" tone="neutral" title="Policies"
        detail={`${deny} blocked and ${approve} approval-gated tool rules pushed to ${policyProfiles.length} profiles first`} />
      <TestsCheck plan={plan} />
      {(plan.warnings ?? []).map((w) => <Check key={w} icon="warning" tone="warning" title="MCP servers" detail={w} />)}
      {(plan.manual_steps ?? []).length > 0 && (
        <Check icon="clock" tone="warning" title="After the apply, on the host"
          detail={`Each profile signs in to an OAuth server itself: ${(plan.manual_steps ?? []).map((m) => m.step).join("; ")}`} />
      )}
    </Card>
  );
}

function TestsCheck({ plan }: { plan: Plan }) {
  const { data: pre } = useLoad(() => api.planPreflight(plan.id), [plan.id, plan.status]);
  if (!pre) return <Check icon="flask" tone="neutral" title="Tests" detail="Checking the suite…" />;
  if (!pre.total) {
    return <Check icon="flask" tone="neutral" title="Tests"
      detail={pre.deferred.length ? `Only workflow tests (${pre.deferred.length}); they run with Workflows` : "This blueprint version has no tests"} />;
  }
  const all = pre.passed === pre.total;
  const detail = `${pre.passed} of ${pre.total} passed on lab or staging`
    + (pre.required ? (pre.satisfied ? " · production gate met" : " · production waits for all of them (Test Lab)") : "");
  return <Check icon={all ? "checkCircle" : pre.required ? "xCircle" : "clock"} tone={all ? "success" : pre.required ? "error" : "warning"} title="Tests" detail={detail} />;
}

function WhatHappens() {
  return (
    <Card className="p-4">
      <h2 className="text-section m-0 mb-3">What happens on apply</h2>
      {["The agent pushes the policy files the plugin enforces", "It snapshots the current Hermes config", "Changes are applied in order; the first failure stops the run", "A drift scan runs right after, against the version just applied"].map((s, n) => (
        <div key={s} className="flex gap-2.5 mb-2 last:mb-0 text-[13px]">
          <span className="size-5 shrink-0 rounded-full bg-primary-tint text-primary text-[11px] font-bold flex items-center justify-center">{n + 1}</span>{s}
        </div>
      ))}
    </Card>
  );
}

function ApplyOutcome({ plan }: { plan: Plan }) {
  const r = plan.apply_result;
  return (
    <Card className="p-4">
      <h2 className="text-section m-0 mb-3">{plan.status === "applying" ? "Applying" : plan.status === "applied" ? "Applied" : "Apply failed"}</h2>
      {plan.status === "applying" && <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Waiting for the agent on {plan.target_instance}…</div>}
      {r?.results?.map((x, n) => (
        <div key={n} className="flex gap-2 text-small py-1">
          <Icon name={x.ok ? "check" : "xCircle"} className={x.ok ? "text-success" : "text-error"} />
          <span><Mono>{x.op}</Mono> {x.profile}{x.error ? <span className="text-error"> — {x.error}</span> : null}</span>
        </div>
      ))}
      {r?.error && <Banner tone="error" className="mt-2">{r.error}</Banner>}
      {r?.snapshot && (
        <div className="mt-3 pt-3 border-t border-hairline text-small text-text-secondary">
          <div className="font-semibold text-text mb-1">Rollback</div>
          Snapshot taken before the apply: <Mono className="break-all">{r.snapshot}</Mono>. Restoring it from here arrives with the rollback job; until then it is restored on the host.
        </div>
      )}
      {plan.status === "applied" && <Link className="block mt-3 text-small" to={`/instances`}>Back to Instances (a drift scan is already queued)</Link>}
    </Card>
  );
}
