// Typed client for the Fleet Control API (apps/api, /api/v1). Shapes mirror fleetcontrol_api/main.py.
// In development Vite proxies /api to the API (see vite.config.ts), so paths stay relative.

export type Environment = "lab" | "staging" | "production";
export type InstanceMode = "agent" | "api-only";

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

async function call<T>(method: string, path: string, body?: unknown, query?: Record<string, string | number>): Promise<T> {
  const qs = query ? "?" + new URLSearchParams(Object.entries(query).map(([k, v]) => [k, String(v)])).toString() : "";
  const res = await fetch(path + qs, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
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

export const api = {
  instances: () => call<Instance[]>("GET", "/api/v1/instances"),
  instance: (id: string) => call<InstanceDetail>("GET", `/api/v1/instances/${enc(id)}`),
  createInstance: (body: { id: string; environment: Environment; mode: InstanceMode }) =>
    call<CreatedInstance>("POST", "/api/v1/instances", body),
  importProfiles: (id: string) => call<{ job_id: string }>("POST", `/api/v1/instances/${enc(id)}/import`),
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
};
