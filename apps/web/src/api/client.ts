// Typed client for the Fleet Control API (apps/api, /api/v1). Shapes mirror fleetcontrol_api/main.py.
// In development Vite proxies /api to the API (see vite.config.ts), so paths stay relative.

export type Environment = "lab" | "staging" | "production";
export type InstanceMode = "agent" | "api-only";
export type RoleName = "admin" | "fleet_architect" | "operator" | "approver" | "viewer";

export interface Me {
  workspace_name?: string;
  email: string;
  name: string;
  role: RoleName;
  role_label: string;
  portal: "admin" | "workspace";
  permissions: string[];
}

export interface RoleInfo {
  role: RoleName;
  label: string;
  description: string;
  members: number;
  portal: "admin" | "workspace";
  permissions: string[];
}

export interface Person {
  email: string;
  name: string;
  role: RoleName;
  role_label: string;
  disabled: boolean;
  created_at: number;
  last_login: number | null;
}

export interface CapabilityReport {
  hermes_version?: string | null;
  config_version?: number;
  surfaces?: { dashboard?: string; api?: string; cli?: string };
  plugins?: { fleetcontrol?: string; langfuse?: string };
  capabilities?: string[];
  dashboard_auth_required?: boolean | null;
  notes?: string[];
}

export interface AppliedVersion {
  name: string;
  version: number;
  plan_id: string;
  at: number;
}

export interface Instance {
  id: string;
  environment: Environment;
  owner: string;
  mode: InstanceMode;
  status: "pending" | "healthy" | "degraded";
  hermes_version: string | null;
  agent_version: string | null;
  capabilities: string[];
  report: CapabilityReport;
  last_heartbeat: number | null;
  created_at: number;
  open_drift: number;
  live_profile_count: number;
  applied: AppliedVersion | null;
}

export interface DriftDiff {
  field: string;
  blueprint: unknown;
  live: unknown;
}
export type DriftMap = Record<string, DriftDiff[]>;

export interface DriftException {
  profile: string;
  field: string;
  expires_at: number;
  by: string;
  reason: string | null;
  created_at: number;
}

export interface DriftReport {
  at: number;
  blueprint: string | null;
  version: number | null;
  drift: DriftMap;
  excepted?: DriftMap;
  ignored?: DriftMap;
  accepted?: DriftMap;
  exceptions?: DriftException[];
}

export interface InstanceDetail extends Instance {
  live_profiles: string[];
  drift: DriftReport | null;
}

export interface CreatedInstance extends Instance {
  pairing_token: string;
  install_command: string;
}

export type DriftAction = "accept" | "revert" | "ignore_once" | "exception";

export interface ResolveResult {
  action: DriftAction;
  resolved: DriftMap;
  plan?: Plan;
  blueprint?: string;
  version?: number;
  status?: string;
  expires_at?: number;
}

export interface SettingsValues {
  workspace_name: string;
  session_hours: number;
  approvals_production: number;
  approvals_staging: number;
  approvals_lab: number;
  require_tests_for_production: boolean;
  token_max_days: number;
}

export interface SettingsDoc {
  values: SettingsValues;
  defaults: SettingsValues;
  updated: { at: number; by: string } | null;
}

export interface ApiToken {
  id: string;
  owner: string;
  name: string;
  created_at: number;
  expires_at: number;
  last_used_at: number | null;
  revoked_at: number | null;
}

export interface ArchitectConstraints {
  data_residency: "any" | "region" | "on-premises";
  cloud_models: "allowed" | "redacted-only" | "none";
  external_actions_need_approval: boolean;
  budget_usd_per_day: number | null;
}

export interface ProposedAgent {
  id: string;
  name: string;
  role: string;
  model: { provider: string; name: string; data_class: "raw" | "redacted-only" };
  soul: { objective: string; principles: string[]; boundaries: string[] };
  skills: string[];
  toolsets: string[];
  mcps: string[];
  delegates_to: string[];
}

export interface Proposal {
  schema: string;
  summary: string;
  agents: ProposedAgent[];
  tests: { id: string; target: string; scenario: string; required_tools: string[]; forbidden_tools: string[] }[];
  open_questions: string[];
  estimate: { cost_per_day_usd: string | null; basis: string | null };
  adjustments: string[];
}

export interface ProposalVersion {
  version: number;
  job_id: string | null;
  job_status?: string;
  status: "running" | "ready" | "failed";
  requested_at: number;
  finished_at: number | null;
  proposal: Proposal | null;
  error: string | null;
  raw: string | null;
  run: { run_id: string | null; usage: Record<string, unknown> | null } | null;
}

export type AgentDecisionValue = "accepted" | "removed";

export interface ArchitectSession {
  id: string;
  created_by: string;
  created_at: number;
  updated_at: number;
  mission: string;
  constraints: ArchitectConstraints;
  architect: { instance_id: string; profile: string };
  answers: { question: string; answer: string; version: number }[];
  versions: ProposalVersion[];
  decisions: Record<string, AgentDecisionValue>;
  edits: Record<string, Partial<ProposedAgent>>;
  blueprint: { name: string; version: number } | null;
  latest: ProposalVersion | null;
  pending: ProposalVersion | null;
}

export interface ArchitectSessionSummary {
  id: string;
  mission: string;
  created_by: string;
  created_at: number;
  updated_at: number;
  versions: number;
  status: "new" | "running" | "ready" | "failed";
  blueprint: { name: string; version: number } | null;
}

export interface ArchitectConfigDoc {
  config: { instance_id: string; profile: string; model: { provider: string; name: string } | null; instance_status: string } | null;
  candidates: { instance_id: string; environment: Environment; profiles: { name: string; model: string | null }[] }[];
}

export type Classification = "internal" | "confidential" | "restricted";

export interface ContentZone {
  id: string;
  name: string;
  description: string;
  read_roles: RoleName[];
  managed: boolean;
  source: string | null;
  created_by: string;
  created_at: number;
  files: number;
  agents: { agent: string; blueprint: string; redacted_only: boolean }[];
  may_read: boolean;
}

export interface ContentFile {
  id: string;
  zone: string;
  name: string;
  classification: Classification;
  size: number | null;
  uploaded_by: string;
  at: number;
  text?: string | null;
}

export type OutputKind = "document" | "structured" | "graph" | "markdown" | "summary";

export interface FleetOutput {
  id: string;
  zone: string;
  name: string;
  kind: OutputKind;
  classification: Classification;
  produced_by: string;
  case: string | null;
  blueprint: string | null;
  instance_id: string | null;
  source: { kind: string; ref?: string } | null;
  size: number | null;
  at: number;
  provenance: string;
  text?: string | null;
}

export type RoomStatus = "open" | "decided" | "cancelled";
export type EvidenceKind = "file" | "output" | "claim" | "note";

export interface RoomOption {
  id: string;
  label: string;
}

export interface RoomEvidence {
  kind: EvidenceKind;
  label: string;
  ref: string | null;
  source: string | null;
  verdict: Verdict | null;
  added_by: string;
  at: number;
}

export interface RoomFinding {
  agent: string;
  text: string;
  verdict: Verdict | null;
  run_id: string | null;
  at: number;
}

export interface RoomDecision {
  by: string;
  option: string;
  rationale: string;
  at: number;
}

export interface RoomOutcome {
  option: RoomOption | null;
  agreed: boolean;
  decisions: number;
}

/** What every room carries, in a list row and in the full view alike. */
export interface RoomBase {
  id: string;
  question: string;
  case: string | null;
  zone: string;
  status: RoomStatus;
  second_approver: string | null;
  opened_by: string;
  opened_by_kind: "person" | "agent";
  instance_id: string | null;
  due_at: number | null;
  created_at: number;
  updated_at: number;
  closed_at: number | null;
  mine: boolean; // this person has already decided
  may_decide: boolean;
  reason: string | null; // why not, when may_decide is false
  waiting_for: string[];
  outcome: RoomOutcome | null;
}

/** GET /api/v1/rooms: evidence, findings and decisions are counts, and there are no options — a row
 *  that needs the chosen option reads it from `outcome.option`, which carries its label. */
export interface DecisionRoomRow extends RoomBase {
  evidence: number;
  findings: number;
  decisions: number;
}

/** GET /api/v1/rooms/{id}: the same room with its contents. */
export interface DecisionRoom extends RoomBase {
  options: RoomOption[];
  evidence: RoomEvidence[];
  findings: RoomFinding[];
  decisions: RoomDecision[];
  counts: { evidence: number; findings: number; decisions: number };
}

export type IntegrationKind = "mcp" | "model";
export type IntegrationHealth = "healthy" | "unreachable" | "disabled" | "unknown";

export interface Integration {
  kind: IntegrationKind;
  name: string;
  instances: string[];
  profiles: string[];
  environments: string[];
  tools: { name: string; description: string }[];
  models: string[];
  used_by: { agent: string; blueprint: string }[];
  allow: Record<string, { blocked_for: string[]; approval_for: string[] }>;
  health: IntegrationHealth;
  error: string | null;
  enabled_everywhere: boolean;
  transport?: string | null;
  endpoint?: string | null;
  auth?: string | null;
}

export interface IntegrationsDoc {
  integrations: Integration[];
  discovery: { instance_id: string; at: number | null; can_discover: boolean }[];
}

export type Verdict = "Evidence found" | "No evidence" | "Not verifiable" | "Policy blocked";
// "cancelled" is someone stopping a run — it says nothing about the test, so it is never a failure.
export type TestStatus = "running" | "passed" | "failed" | "not_verifiable" | "error" | "cancelled";

export interface BlueprintTest {
  id: string;
  target: string;
  scenario: string;
  required_tools: string[];
  forbidden_tools: string[];
  expected_artifact?: string | null;
  evaluator: "schema" | "exact" | "contains" | "artifact-exists" | "none";
  expected?: string | null;
  limits: { max_seconds: number; max_tokens: number; max_cost_usd: number };
}

export interface TestRunSummary {
  id: string;
  status: TestStatus;
  instance_id: string;
  created_at: number;
  finished_at: number | null;
  failure: string | null;
}

export interface SuiteTest {
  test: BlueprintTest;
  target_kind: "agent" | "workflow";
  profile: string | null;
  last_run: TestRunSummary | null;
}

export interface Suite {
  blueprint: string;
  version: number;
  status: string;
  agents: { id: string; profile: string; role: string }[];
  workflows: string[];
  tests: SuiteTest[];
  gates_production: boolean;
  applied_on: string[];
}

export interface TestCheck {
  id: string;
  kind: string;
  subject: string;
  outcome: "pass" | "fail" | "not_verifiable";
  detail: string;
}

export interface Claim {
  claim: string;
  verdict: Verdict;
  check: string | null;
  detail: string;
}

export interface ToolCallEvidence {
  name: string;
  arguments: string;
  result: string | null;
  answered: boolean;
}

export interface TestRun {
  id: string;
  blueprint: string;
  version: number;
  test_id: string;
  target: string;
  profile: string;
  instance_id: string;
  environment: Environment;
  status: TestStatus;
  created_at: number;
  created_by: string;
  finished_at: number | null;
  checks: TestCheck[];
  claims: Claim[];
  output: string | null;
  tool_calls: ToolCallEvidence[] | null;
  usage: Record<string, number> | null;
  duration_s: number | null;
  error: string | null;
  evidence: string | null;
  evidence_error: string | null;
  notes: string[];
}

export interface ClaimRow {
  id: string;
  run_id: string;
  at: number;
  instance_id: string;
  blueprint: string;
  version: number;
  test_id: string;
  agent: string;
  claim: string;
  verdict: Verdict;
  detail: string;
  check: string | null;
}

export interface AssuranceSummary {
  hours: number;
  total: number;
  counts: Record<Verdict, number>;
  instances: number;
  most_not_verifiable: string | null;
}

export interface Preflight {
  total: number;
  passed: number;
  tests: { test_id: string; status: TestStatus | "not_run"; instance_id: string | null; at: number | null }[];
  deferred: string[];
  required: boolean;
  satisfied: boolean;
}

export type TestEditBody = Partial<Omit<BlueprintTest, "id">>;

export interface LiveImportResult {
  name: string;
  version: number;
  status: string;
  managed_profiles: string[];
  skipped: { profile: string; reason: string }[];
}

export interface BlueprintSummary {
  name: string;
  versions: number[];
  latest: number;
  status: string;
  owner: string;
  description: string | null;
  agents: number;
  workflows: number;
  tests: number;
  updated_at: number;
  author: string;
  applied_on: { instance: string; version: number; at: number }[];
}

export interface BlueprintVersionInfo {
  version: number;
  status: string;
  author: string;
  created_at: number;
}

export interface BlueprintVersion {
  name: string;
  version: number;
  status: string;
  yaml: string;
}

// ---- the parsed blueprint (packages/blueprint_schema, model_dump(mode="json")) ----

export interface ModelPolicy {
  provider: string;
  name: string;
  fallback?: string | null;
  data_class: "raw" | "redacted-only";
}

export interface OutputContract {
  format: "json" | "markdown" | "text" | "file";
  required: string[];
  artifact?: string | null;
}

export interface SoulDoc {
  objective: string;
  principles: string[];
  boundaries: string[];
  output_contract?: OutputContract | null;
  raw?: string | null;
}

export interface AgentDoc {
  id: string;
  role: string;
  model: ModelPolicy;
  soul: SoulDoc;
  skills: string[];
  toolsets: string[];
  mcps: string[];
  delegates_to: string[];
  content_zones: string[];
  tests: string[];
  hermes_profile?: string | null;
}

export interface TestDoc {
  id: string;
  target: string;
  scenario: string;
  required_tools: string[];
  forbidden_tools: string[];
  expected_artifact?: string | null;
  evaluator: string;
  expected?: string | null;
}

export interface AgentStepDoc { agent: string; artifact?: string | null; input?: string | null }
export interface ParallelStepDoc { parallel: AgentStepDoc[] }
export interface HumanGateDoc { human_gate: string; timeout: string; escalate_to?: string | null; on_reject?: string | null }
export interface DecisionRoomStepDoc { open_decision_room: boolean; question_template?: string | null }
export type WorkflowStepDoc = AgentStepDoc | ParallelStepDoc | HumanGateDoc | DecisionRoomStepDoc;

export interface WorkflowDoc {
  id: string;
  steps: WorkflowStepDoc[];
  tests: string[];
}

export interface PolicyDoc {
  id: string;
  kind: string;
  description: string;
  applies_to: string[];
  params?: Record<string, unknown>;
  enforcement: "block" | "approve" | "flag";
}

export interface BlueprintDoc {
  metadata: { name: string; version: number; owner: string; description?: string | null };
  mission: string;
  policies: PolicyDoc[];
  agents: AgentDoc[];
  workflows: WorkflowDoc[];
  tests: TestDoc[];
  delivery: { when: string; to: string; template: string; enabled: boolean }[];
  targets: { instance: string; environment: Environment; requires_approvals: number }[];
}

export interface ManagedFields {
  description: string;
  model: { provider: string; name: string };
  soul_sha256: string;
  skills: string[];
  toolsets: string[];
  mcps: string[];
}

export interface BlueprintDetail extends BlueprintVersion {
  parsed: BlueprintDoc;
  managed: Record<string, ManagedFields>;
  author: string;
  created_at: number;
}

export type AgentPatch = Partial<Pick<AgentDoc, "role" | "model" | "soul" | "skills" | "toolsets" | "mcps" | "delegates_to" | "content_zones" | "tests">>;

export const AUDIT_EXPORT_URL = "/api/v1/audit/export";

export interface PlanRow {
  kind: "create" | "update" | "remove" | "approval";
  symbol: string;
  object: string;
  description: string;
  method: string;
  risk: "low" | "medium" | "high";
  ops: Record<string, unknown>[];
}

export interface ApplyResult {
  ok?: boolean;
  snapshot?: string | null;
  results?: { op: string; profile?: string; ok: boolean; error?: string }[];
  stopped_at?: number;
  error?: string;
}

export interface Plan {
  id: string;
  target_instance: string;
  environment: Environment;
  blueprint: { name: string; version: number };
  changes: PlanRow[];
  no_change: { profiles: string[] };
  unmanaged_profiles: string[];
  approvals_required: number;
  approvals: string[];
  policy_push: Record<string, { deny_tools: string[]; approve_tools: string[] }>;
  can_apply: boolean;
  blocked_reason: string | null;
  status: "planned" | "applying" | "applied" | "failed";
  apply_result?: ApplyResult;
  created_by?: string;
  created_at?: number;
}

export interface PlanSummary {
  id: string;
  target_instance: string;
  environment: Environment;
  blueprint: { name: string; version: number };
  status: Plan["status"];
  approvals: string[];
  approvals_required: number;
  created_by: string;
  created_at: number;
  changes: number;
}

export interface Job {
  id: string;
  instance_id: string;
  kind: string;
  status: "queued" | "running" | "done" | "failed";
  result: Record<string, unknown> | null;
  created_at: number;
  finished_at?: number;
}

export interface AuditEvent {
  id: string;
  ts: number;
  actor: string;
  action: string;
  target: string;
  detail: string;
}

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** FastAPI errors come as {"detail": "..."} or, for validation, {"detail": [{"msg": "..."}]}. */
export function detailOf(data: unknown): string | null {
  if (!data || typeof data !== "object" || !("detail" in data)) return null;
  const d = (data as { detail: unknown }).detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e) => (e && typeof e === "object" && "msg" in e ? String(e.msg) : String(e))).join("; ");
  return null;
}

/** Fired when the API answers 401: the session ended, so the app shows Sign in again. */
export const UNAUTHORIZED_EVENT = "fc:unauthorized";

async function call<T>(method: string, path: string, body?: unknown, query?: Record<string, string | number>): Promise<T> {
  const qs = query ? "?" + new URLSearchParams(Object.entries(query).map(([k, v]) => [k, String(v)])).toString() : "";
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-Fleet-Control"] = "1"; // the API refuses writes without it (a cross-site form cannot send it)
  const res = await fetch(path + qs, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401 && path !== "/api/v1/auth/login" && typeof window !== "undefined") {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) throw new ApiError(res.status, detailOf(data) ?? `${method} ${path} failed (${res.status})`);
  return data as T;
}

const enc = encodeURIComponent;

/** Query parameters without the unset ones. */
function params(o: Record<string, string | number | undefined>): Record<string, string | number> {
  return Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined && v !== "")) as Record<string, string | number>;
}

export const api = {
  me: () => call<Me>("GET", "/api/v1/auth/me"),
  login: (email: string, password: string) => call<Me>("POST", "/api/v1/auth/login", { email, password }),
  logout: () => call<{ ok: boolean }>("POST", "/api/v1/auth/logout"),
  changePassword: (current: string, next: string) => call<{ ok: boolean }>("POST", "/api/v1/auth/password", { current, new: next }),
  roles: () => call<RoleInfo[]>("GET", "/api/v1/roles"),
  users: () => call<Person[]>("GET", "/api/v1/users"),
  createUser: (body: { email: string; name: string; role: RoleName }) =>
    call<{ user: Person; password: string }>("POST", "/api/v1/users", body),
  updateUser: (email: string, body: Partial<{ name: string; role: RoleName; disabled: boolean }>) =>
    call<Person>("PATCH", `/api/v1/users/${encodeURIComponent(email)}`, body),
  resetPassword: (email: string) => call<{ password: string }>("POST", `/api/v1/users/${encodeURIComponent(email)}/reset-password`),
  plans: (status?: Plan["status"]) => call<PlanSummary[]>("GET", "/api/v1/plans", undefined, status ? { status } : undefined),
  instances: () => call<Instance[]>("GET", "/api/v1/instances"),
  instance: (id: string) => call<InstanceDetail>("GET", `/api/v1/instances/${enc(id)}`),
  createInstance: (body: { id: string; environment: Environment; mode: InstanceMode }) =>
    call<CreatedInstance>("POST", "/api/v1/instances", body),
  editInstance: (id: string, body: { environment?: Environment; owner?: string }) =>
    call<Instance>("PATCH", `/api/v1/instances/${enc(id)}`, body),
  removeInstance: (id: string) =>
    call<{ ok: boolean; id: string; removed: Record<string, number>; had_applied: { name: string; version: number } | null }>(
      "DELETE", `/api/v1/instances/${enc(id)}`),
  importProfiles: (id: string) => call<{ job_id: string }>("POST", `/api/v1/instances/${enc(id)}/import`),
  blueprintFromLive: (id: string, body: { name: string; profiles?: string[] }) =>
    call<LiveImportResult>("POST", `/api/v1/instances/${enc(id)}/blueprint-from-live`, body),
  driftScan: (id: string, blueprint: string, version: number) =>
    call<{ job_id: string }>("POST", `/api/v1/instances/${enc(id)}/drift-scan`, undefined, { blueprint, version }),
  drift: async (id: string): Promise<DriftReport | null> => {
    try {
      return await call<DriftReport>("GET", `/api/v1/instances/${enc(id)}/drift`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  },
  resolveDrift: (
    id: string,
    body: { action: DriftAction; fields?: { profile: string; field: string }[]; expires_at?: number; reason?: string },
  ) => call<ResolveResult>("POST", `/api/v1/instances/${enc(id)}/drift/resolve`, body),
  blueprints: () => call<BlueprintSummary[]>("GET", "/api/v1/blueprints"),
  blueprintVersions: (name: string) => call<BlueprintVersionInfo[]>("GET", `/api/v1/blueprints/${enc(name)}`),
  blueprint: (name: string, version: number) => call<BlueprintDetail>("GET", `/api/v1/blueprints/${enc(name)}/${version}`),
  createDraft: (name: string, version: number) =>
    call<{ name: string; version: number; status: string }>("POST", `/api/v1/blueprints/${enc(name)}/${version}/draft`),
  editAgent: (name: string, version: number, agentId: string, patch: AgentPatch) =>
    call<{ name: string; version: number; status: string; changed: string[]; agent: AgentDoc; managed: ManagedFields }>(
      "PUT", `/api/v1/blueprints/${enc(name)}/${version}/agents/${enc(agentId)}`, patch),
  uploadBlueprint: (yaml: string) =>
    call<{ name: string; version: number; status: string; managed_profiles: string[] }>("POST", "/api/v1/blueprints", { yaml }),
  createPlan: (blueprint: string, version: number, instance_id: string) =>
    call<Plan>("POST", "/api/v1/plans", { blueprint, version, instance_id }),
  plan: (id: string) => call<Plan>("GET", `/api/v1/plans/${enc(id)}`),
  approvePlan: (id: string) => call<{ approvals: string[]; required: number }>("POST", `/api/v1/plans/${enc(id)}/approve`),
  applyPlan: (id: string) => call<{ jobs: string[] }>("POST", `/api/v1/plans/${enc(id)}/apply`),
  job: (id: string) => call<Job>("GET", `/api/v1/jobs/${enc(id)}`),
  audit: (limit = 200) => call<AuditEvent[]>("GET", "/api/v1/audit", undefined, { limit }),
  settings: () => call<SettingsDoc>("GET", "/api/v1/settings"),
  updateSettings: (patch: Partial<SettingsValues>) => call<SettingsDoc>("PATCH", "/api/v1/settings", patch),
  tokens: (everyone = false) => call<ApiToken[]>("GET", "/api/v1/tokens", undefined, everyone ? { all_people: "true" } : undefined),
  createToken: (body: { name: string; expires_days: number }) => call<{ token: ApiToken; secret: string }>("POST", "/api/v1/tokens", body),
  revokeToken: (id: string) => call<ApiToken>("DELETE", `/api/v1/tokens/${enc(id)}`),
  outputs: (q: { zone?: string; case?: string; limit?: number } = {}) => call<FleetOutput[]>("GET", "/api/v1/outputs", undefined, params(q)),
  output: (id: string) => call<FleetOutput>("GET", `/api/v1/outputs/${enc(id)}`),
  saveOutput: (body: { zone: string; name: string; kind?: OutputKind; classification?: Classification; text?: string; case?: string }) =>
    call<FleetOutput>("POST", "/api/v1/outputs", body),
  deleteOutput: (id: string) => call<{ ok: boolean }>("DELETE", `/api/v1/outputs/${enc(id)}`),
  rooms: (q: { status?: RoomStatus; case?: string; mine?: boolean } = {}) =>
    call<DecisionRoomRow[]>("GET", "/api/v1/rooms", undefined,
      params({ status: q.status, case: q.case, mine: q.mine ? "true" : undefined })),
  room: (id: string) => call<DecisionRoom>("GET", `/api/v1/rooms/${enc(id)}`),
  openRoom: (body: { question: string; zone: string; options: string[]; case?: string; due_at?: number; second_approver?: string }) =>
    call<DecisionRoom>("POST", "/api/v1/rooms", body),
  addEvidence: (id: string, body: { kind: EvidenceKind; label: string; ref?: string; source?: string; verdict?: Verdict }) =>
    call<DecisionRoom>("POST", `/api/v1/rooms/${enc(id)}/evidence`, body),
  decideRoom: (id: string, body: { option: string; rationale: string }) =>
    call<DecisionRoom>("POST", `/api/v1/rooms/${enc(id)}/decide`, body),
  cancelRoom: (id: string) => call<DecisionRoom>("POST", `/api/v1/rooms/${enc(id)}/cancel`),
  contentZones: () => call<ContentZone[]>("GET", "/api/v1/content/zones"),
  createZone: (body: { id: string; name: string; description?: string; read_roles?: RoleName[] }) =>
    call<ContentZone>("POST", "/api/v1/content/zones", body),
  editZone: (id: string, body: { name?: string; description?: string; read_roles?: RoleName[] }) =>
    call<ContentZone>("PATCH", `/api/v1/content/zones/${enc(id)}`, body),
  deleteZone: (id: string) => call<{ ok: boolean }>("DELETE", `/api/v1/content/zones/${enc(id)}`),
  contentFiles: (zone?: string) => call<ContentFile[]>("GET", "/api/v1/content/files", undefined, params({ zone })),
  contentFile: (id: string) => call<ContentFile>("GET", `/api/v1/content/files/${enc(id)}`),
  uploadFile: (body: { zone: string; name: string; classification: Classification; text?: string }) =>
    call<ContentFile>("POST", "/api/v1/content/files", body),
  deleteFile: (id: string) => call<{ ok: boolean }>("DELETE", `/api/v1/content/files/${enc(id)}`),
  integrations: () => call<IntegrationsDoc>("GET", "/api/v1/integrations"),
  discoverIntegrations: (instance_id: string) => call<{ job_id: string }>("POST", "/api/v1/integrations/discover", { instance_id }),
  addMcpServer: (body: { instance_id: string; profile: string; name: string; url?: string; command?: string; args?: string[]; auth?: "none" | "oauth" }) =>
    call<{ job_id: string }>("POST", "/api/v1/integrations/mcp", body),
  changeMcpServer: (server: string, body: { instance_id: string; profile: string; enabled?: boolean; remove?: boolean }) =>
    call<{ job_id: string }>("PATCH", `/api/v1/integrations/mcp/${enc(server)}`, body),
  testSuites: () => call<Suite[]>("GET", "/api/v1/testlab/suites"),
  testRuns: (q: { blueprint?: string; test_id?: string; instance_id?: string; limit?: number } = {}) =>
    call<TestRun[]>("GET", "/api/v1/testlab/runs", undefined, params(q)),
  testRun: (id: string) => call<TestRun>("GET", `/api/v1/testlab/runs/${enc(id)}`),
  cancelTestRun: (id: string) => call<TestRun>("POST", `/api/v1/testlab/runs/${enc(id)}/cancel`),
  deleteTestRun: (id: string) => call<{ ok: boolean; id: string }>("DELETE", `/api/v1/testlab/runs/${enc(id)}`),
  startTestRuns: (body: { blueprint: string; version: number; instance_id: string; test_ids?: string[] }) =>
    call<{ runs: string[]; skipped: { test_id: string; reason: string }[] }>("POST", "/api/v1/testlab/runs", body),
  saveTest: (name: string, version: number, testId: string, body: TestEditBody) =>
    call<{ name: string; version: number; created: boolean; test: BlueprintTest }>("PUT", `/api/v1/blueprints/${enc(name)}/${version}/tests/${enc(testId)}`, body),
  deleteTest: (name: string, version: number, testId: string) =>
    call<{ ok: boolean }>("DELETE", `/api/v1/blueprints/${enc(name)}/${version}/tests/${enc(testId)}`),
  assuranceClaims: (q: { verdict?: Verdict; instance_id?: string; hours?: number } = {}) =>
    call<ClaimRow[]>("GET", "/api/v1/assurance/claims", undefined, params(q)),
  assuranceSummary: () => call<AssuranceSummary>("GET", "/api/v1/assurance/summary"),
  planPreflight: (id: string) => call<Preflight>("GET", `/api/v1/plans/${enc(id)}/preflight`),
  architectConfig: () => call<ArchitectConfigDoc>("GET", "/api/v1/architect/config"),
  setArchitectConfig: (body: { instance_id: string; profile: string }) => call<ArchitectConfigDoc>("PUT", "/api/v1/architect/config", body),
  architectAsset: (instance_id: string) =>
    call<{ name: string; version: number; status: string }>("POST", "/api/v1/architect/blueprint-asset", { instance_id }),
  architectSessions: () => call<ArchitectSessionSummary[]>("GET", "/api/v1/architect/sessions"),
  architectSession: (id: string) => call<ArchitectSession>("GET", `/api/v1/architect/sessions/${enc(id)}`),
  startArchitect: (body: { mission: string; constraints: ArchitectConstraints }) =>
    call<ArchitectSession>("POST", "/api/v1/architect/sessions", body),
  askArchitect: (id: string, body: { answers: { question: string; answer: string }[] }) =>
    call<ArchitectSession>("POST", `/api/v1/architect/sessions/${enc(id)}/ask`, body),
  decideAgent: (id: string, agentId: string, body: { decision?: AgentDecisionValue | "proposed"; edit?: Partial<ProposedAgent> }) =>
    call<ArchitectSession>("PATCH", `/api/v1/architect/sessions/${enc(id)}/agents/${enc(agentId)}`, body),
  saveArchitectBlueprint: (id: string, name: string) =>
    call<{ name: string; version: number; status: string; agents: string[] }>("POST", `/api/v1/architect/sessions/${enc(id)}/blueprint`, { name }),
};
