import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import { api, type BlueprintTest, type Suite, type TestRun } from "../api/client";
import { useAuth } from "../lib/auth";
import { errorText, useLoad, useNow } from "../lib/hooks";
import { EVALUATOR_LABEL, VERDICT_TONE, lastRunSummary, newTest, statusChip, suiteGroups, testIdFrom } from "../lib/testlab";
import { formatDateTime, timeAgo, type Tone } from "../lib/view";
import { Banner, Button, Card, Chip, Field, INPUT, Icon, KpiTile, Modal, Mono, PageHeader, Spinner, TEXTAREA, TagInput } from "../components/ui";

// design/screens/TestLab: suites · test editor · recent runs
export function TestLabScreen() {
  const { can } = useAuth();
  const now = useNow();
  const [search, setSearch] = useSearchParams();
  const { data: suites, error, reload } = useLoad(api.testSuites, [], 8000);
  const { data: instances } = useLoad(api.instances, []);
  const targets = (instances ?? []).filter((i) => i.environment !== "production" && i.mode === "agent" && i.agent_version);
  const [instance, setInstance] = useState("");
  const runOn = targets.some((i) => i.id === instance) ? instance : targets[0]?.id ?? "";
  const [creating, setCreating] = useState<BlueprintTest | null>(null);
  const [naming, setNaming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: Tone; text: string } | null>(null);
  const [openRun, setOpenRun] = useState<string | null>(null);
  const [runsKey, setRunsKey] = useState(0);

  const all = suites ?? [];
  const withTests = all.filter((s) => s.tests.length > 0);
  const suite = all.find((s) => s.blueprint === search.get("blueprint")) ?? withTests[0] ?? all[0] ?? null;
  const selectedId = search.get("test") ?? suite?.tests[0]?.test.id ?? null;
  const current = creating ?? suite?.tests.find((t) => t.test.id === selectedId)?.test ?? null;
  const summary = lastRunSummary(all);
  const gating = all.filter((s) => s.gates_production);

  function select(blueprint: string, test: string | null) {
    setCreating(null);
    setMsg(null);
    setSearch(test ? { blueprint, test } : { blueprint });
  }

  async function run(testIds?: string[]) {
    if (!suite || !runOn) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.startTestRuns({ blueprint: suite.blueprint, version: suite.version, instance_id: runOn, test_ids: testIds });
      const skipped = r.skipped.map((s) => `${s.test_id}: ${s.reason}`).join("; ");
      setMsg({ tone: r.runs.length ? "info" : "warning",
        text: `${r.runs.length} test run${r.runs.length === 1 ? "" : "s"} queued on ${runOn}.${skipped ? ` Skipped ${skipped}.` : ""}` });
      setRunsKey((k) => k + 1);
      await reload();
    } catch (e) {
      setMsg({ tone: "error", text: errorText(e) });
    } finally {
      setBusy(false);
    }
  }

  const header = (
    <PageHeader crumb="Operate" title="Test Lab" subtitle="Tests run against a lab or staging instance and gate production plans."
      actions={<>
        {targets.length > 0 && (
          <select aria-label="Run on instance" className={`${INPUT} h-9 w-[220px]`} value={runOn} onChange={(e) => setInstance(e.target.value)}>
            {targets.map((i) => <option key={i.id} value={i.id}>Run on {i.id} ({i.environment})</option>)}
          </select>
        )}
        {can("tests.run") && (
          <Button icon="flask" disabled={!suite?.tests.length || !runOn || busy} onClick={() => void run()}
            title={runOn ? undefined : "Connect a lab or staging instance with a Fleet Control Agent"}>
            {busy && <Spinner />}Run suite
          </Button>
        )}
        {can("blueprints.write") && <Button variant="primary" icon="plus" disabled={!suite} onClick={() => setNaming(true)}>New test</Button>}
      </>} />
  );

  if (!suites) return <>{header}{error ? <Banner tone="error">{error}</Banner> : <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading suites…</div>}</>;

  return (
    <>
      {header}
      {msg && <Banner tone={msg.tone} className="mb-4">{msg.text}</Banner>}
      {!runOn && <Banner tone="warning" className="mb-4">No lab or staging instance with a paired Fleet Control Agent: tests cannot run until one is connected.</Banner>}

      <div className="grid grid-cols-3 gap-4 mb-5">
        <KpiTile label="Last run" value={summary.ran ? `${summary.passed}` : "—"} unit={summary.ran ? `of ${summary.ran} passed` : undefined}
          tone={!summary.ran ? "neutral" : summary.passed === summary.ran ? "success" : "warning"}
          note={summary.at ? `latest on ${summary.instance}, ${timeAgo(summary.at, now)}` : "No test has run yet"} />
        <KpiTile label="Suites" value={withTests.length}
          note={`${withTests.reduce((n, s) => n + s.tests.length, 0)} tests across ${withTests.map((s) => s.blueprint).join(", ") || "no blueprint yet"}`} />
        <KpiTile label="Gating plans" value={gating.length}
          note={gating.length ? `${gating.map((s) => `${s.blueprint} v${s.version}`).join(", ")} · must pass before a production apply` : "Settings → Approvals turns the gate on"} />
      </div>

      <div className="flex gap-5 items-start">
        <SuitesTree suites={all} selected={suite ? { blueprint: suite.blueprint, test: current?.id ?? null } : null} onSelect={select} />
        <div className="flex-1 min-w-0">
          {suite && current ? (
            <TestEditor key={`${suite.blueprint}:${suite.version}:${current.id}:${creating ? "new" : "saved"}`} suite={suite} test={current}
              isNew={Boolean(creating)} runOn={runOn} busy={busy}
              onRun={() => void run([current.id])}
              onSaved={async (id) => { setCreating(null); await reload(); select(suite.blueprint, id); }}
              onDeleted={async () => { setCreating(null); await reload(); select(suite.blueprint, null); }}
              onDraft={async () => { await reload(); }} />
          ) : (
            <Card className="p-6 text-text-secondary">
              {suite ? `${suite.blueprint} v${suite.version} has no tests yet. Add one with New test.` : "No blueprint yet. Import or create one in Blueprints."}
            </Card>
          )}
        </div>
        {suite && current && !creating && (
          <RecentRuns key={`${suite.blueprint}:${current.id}:${runsKey}`} blueprint={suite.blueprint} testId={current.id} now={now} onOpen={setOpenRun} />
        )}
      </div>

      {naming && suite && (
        <NewTestModal suite={suite} onClose={() => setNaming(false)}
          onCreate={(test) => { setNaming(false); setCreating(test); setSearch({ blueprint: suite.blueprint }); }} />
      )}
      {openRun && <RunModal runId={openRun} onClose={() => setOpenRun(null)} onChanged={() => { setRunsKey((k) => k + 1); void reload(); }} />}
    </>
  );
}

function SuitesTree({ suites, selected, onSelect }: {
  suites: Suite[]; selected: { blueprint: string; test: string | null } | null; onSelect: (blueprint: string, test: string | null) => void;
}) {
  const total = suites.reduce((n, s) => n + s.tests.length, 0);
  return (
    <Card className="w-[300px] shrink-0 overflow-hidden">
      <div className="flex justify-between items-center px-4 py-3.5 border-b border-hairline">
        <span className="text-[15px] font-semibold">Suites</span>
        <span className="text-small text-text-secondary">{total} tests</span>
      </div>
      <div className="p-2 flex flex-col gap-0.5">
        {suites.map((s) => (
          <div key={s.blueprint}>
            <button type="button" onClick={() => onSelect(s.blueprint, null)}
              className="w-full h-[34px] flex items-center gap-2 px-2.5 rounded-control text-[13px] font-semibold text-left cursor-pointer hover:bg-container-low">
              <Icon name="folder" className="text-text-secondary" />
              <span className="truncate">{s.blueprint}</span>
              <span className="font-normal text-text-secondary">v{s.version} · {s.tests.length}</span>
            </button>
            {selected?.blueprint === s.blueprint && suiteGroups(s).map((g) => (
              <div key={g.key}>
                <div className="h-7 flex items-center gap-2 pl-8 pr-2.5 text-small text-text-secondary">
                  <Icon name={g.kind === "agent" ? "bot" : "flow"} size={14} />{g.label}
                </div>
                {g.tests.map((t) => {
                  const chip = statusChip(t.last_run?.status ?? "not_run");
                  const on = selected.test === t.test.id;
                  return (
                    <button key={t.test.id} type="button" onClick={() => onSelect(s.blueprint, t.test.id)}
                      className={`w-full h-[32px] flex items-center justify-between gap-2 pl-12 pr-2.5 rounded-control text-[13px] text-left cursor-pointer ${on ? "bg-primary-tint text-primary font-semibold" : "hover:bg-container-low"}`}>
                      <span className="truncate font-mono text-[12px]">{t.test.id}</span>
                      <span className={`size-2 shrink-0 rounded-full ${chip.tone === "success" ? "bg-success" : chip.tone === "error" ? "bg-error" : chip.tone === "info" ? "bg-primary" : "bg-outline"}`} title={chip.label} />
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
        ))}
        {suites.length === 0 && <p className="m-0 px-2.5 py-3 text-small text-text-secondary">No blueprints yet.</p>}
      </div>
    </Card>
  );
}

function Row({ label, children, top = false }: { label: string; children: React.ReactNode; top?: boolean }) {
  return (
    <div className={`grid grid-cols-[130px_minmax(0,1fr)] gap-3 px-3.5 py-2.5 border-b border-hairline last:border-b-0 ${top ? "items-start" : "items-center"}`}>
      <div className={`text-label uppercase text-text-secondary ${top ? "pt-2.5" : ""}`}>{label}</div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function TestEditor({ suite, test, isNew, runOn, busy, onRun, onSaved, onDeleted, onDraft }: {
  suite: Suite; test: BlueprintTest; isNew: boolean; runOn: string; busy: boolean;
  onRun: () => void; onSaved: (id: string) => Promise<void>; onDeleted: () => Promise<void>; onDraft: () => Promise<void>;
}) {
  const { can } = useAuth();
  const editable = can("blueprints.write") && suite.status === "draft";
  const [target, setTarget] = useState(test.target);
  const [scenario, setScenario] = useState(test.scenario);
  const [required, setRequired] = useState(test.required_tools);
  const [forbidden, setForbidden] = useState(test.forbidden_tools);
  const [artifact, setArtifact] = useState(test.expected_artifact ?? "");
  const [evaluator, setEvaluator] = useState(test.evaluator);
  const [expected, setExpected] = useState(test.expected ?? "");
  const [limits, setLimits] = useState(test.limits);
  const [saving, setSaving] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const targets = [...suite.agents.map((a) => a.id), ...suite.workflows];
  const isWorkflow = suite.workflows.includes(target);

  async function act(key: string, fn: () => Promise<void>) {
    setSaving(key);
    setFailure(null);
    try {
      await fn();
    } catch (e) {
      setFailure(errorText(e));
    } finally {
      setSaving(null);
    }
  }

  function save(e: FormEvent) {
    e.preventDefault();
    void act("save", async () => {
      await api.saveTest(suite.blueprint, suite.version, test.id, {
        target, scenario: scenario.trim(), required_tools: required, forbidden_tools: forbidden,
        expected_artifact: artifact.trim() || null, evaluator, expected: ["contains", "exact"].includes(evaluator) ? expected : null, limits,
      });
      await onSaved(test.id);
    });
  }

  return (
    <Card className="p-[18px] flex flex-col gap-4">
      <div className="flex justify-between items-start gap-3">
        <div className="min-w-0">
          <div className="text-[15px] font-semibold truncate">{isNew ? "New test" : test.id}</div>
          <div className="text-small text-text-secondary">{target} · <Mono>{test.id}</Mono> · {suite.blueprint} v{suite.version}</div>
        </div>
        {!isNew && can("tests.run") && (
          <Button icon="flask" disabled={!runOn || busy || isWorkflow} onClick={onRun}
            title={isWorkflow ? "Workflow tests run with Workflows (Slice 5)" : runOn ? `Run on ${runOn}` : "No lab or staging instance"}>Run now</Button>
        )}
      </div>

      {suite.status !== "draft" && can("blueprints.write") && (
        <Banner tone="info">
          <span className="flex items-center justify-between gap-3">
            <span>{suite.blueprint} v{suite.version} is {suite.status} and immutable. Tests are edited on a draft.</span>
            <Button disabled={saving !== null} onClick={() => void act("draft", async () => { await api.createDraft(suite.blueprint, suite.version); await onDraft(); })}>
              {saving === "draft" && <Spinner />}Create draft
            </Button>
          </span>
        </Banner>
      )}

      <form id="test-form" onSubmit={save} className="border border-hairline rounded-control">
        <Row label="Target">
          <select className={INPUT} aria-label="Target" disabled={!editable} value={target} onChange={(e) => setTarget(e.target.value)}>
            {targets.map((t) => <option key={t} value={t}>{suite.workflows.includes(t) ? `Workflow: ${t}` : t}</option>)}
          </select>
        </Row>
        <Row label="Scenario input" top>
          <textarea className={`${TEXTAREA} min-h-[64px]`} aria-label="Scenario input" disabled={!editable} value={scenario}
            onChange={(e) => setScenario(e.target.value)} placeholder="What the agent receives, e.g. Screen “Zephyr Maritime Ltd” and its two directors." />
        </Row>
        <Row label="Required tool calls">
          {editable ? <TagInput label="Required tool calls" values={required} onChange={setRequired} placeholder="e.g. opensanctions.search" />
            : <ToolChips tools={required} tone="success" />}
        </Row>
        <Row label="Forbidden tools">
          {editable ? <TagInput label="Forbidden tools" values={forbidden} onChange={setForbidden} placeholder="e.g. web.fetch" />
            : <ToolChips tools={forbidden} tone="error" />}
        </Row>
        <Row label="Expected artifact">
          <input className={INPUT} aria-label="Expected artifact" disabled={!editable} value={artifact} onChange={(e) => setArtifact(e.target.value)} placeholder="e.g. screening-result.json" />
        </Row>
        <Row label="Limits">
          <span className="flex items-center gap-2 flex-wrap text-small">
            ≤ <input type="number" min={1} aria-label="Max seconds" className={`${INPUT} h-8 w-20`} disabled={!editable} value={limits.max_seconds}
              onChange={(e) => setLimits({ ...limits, max_seconds: Number(e.target.value) })} /> s
            <span className="ml-2">≤</span> <input type="number" min={1} aria-label="Max tokens" className={`${INPUT} h-8 w-24`} disabled={!editable} value={limits.max_tokens}
              onChange={(e) => setLimits({ ...limits, max_tokens: Number(e.target.value) })} /> tokens
          </span>
        </Row>
        <Row label="Evaluator">
          <div className="flex flex-col gap-2">
            <select className={INPUT} aria-label="Evaluator" disabled={!editable} value={evaluator} onChange={(e) => setEvaluator(e.target.value as BlueprintTest["evaluator"])}>
              {(Object.keys(EVALUATOR_LABEL) as BlueprintTest["evaluator"][]).map((k) => <option key={k} value={k}>{EVALUATOR_LABEL[k]}</option>)}
            </select>
            {["contains", "exact"].includes(evaluator) && (
              <input className={INPUT} aria-label="Expected text" disabled={!editable} value={expected} onChange={(e) => setExpected(e.target.value)} placeholder="Expected text" />
            )}
            <span className="text-small text-text-secondary">Deterministic, no model judge: checked against the run's Hermes session transcript.</span>
          </div>
        </Row>
      </form>

      {failure && <Banner tone="error">{failure}</Banner>}
      <div className="flex justify-between items-center gap-3 text-small text-text-secondary">
        <span>{suite.gates_production ? <>Gates: <strong className="text-text">{suite.blueprint} v{suite.version}</strong> (production applies)</> : "Does not gate production applies"}</span>
        {editable && (
          <span className="flex gap-2">
            {!isNew && <Button variant="danger" disabled={saving !== null}
              onClick={() => window.confirm(`Delete test ${test.id} from ${suite.blueprint} v${suite.version}?`) && void act("delete", async () => { await api.deleteTest(suite.blueprint, suite.version, test.id); await onDeleted(); })}>
              Delete</Button>}
            <Button variant="primary" type="submit" form="test-form" disabled={saving !== null || !scenario.trim()}>{saving === "save" && <Spinner />}{isNew ? "Add test" : "Save"}</Button>
          </span>
        )}
      </div>
    </Card>
  );
}

function ToolChips({ tools, tone }: { tools: string[]; tone: "success" | "error" }) {
  if (!tools.length) return <span className="text-small text-text-secondary">None</span>;
  return (
    <span className="flex flex-wrap gap-1.5">
      {tools.map((t) => (
        <span key={t} className={`inline-flex items-center h-[22px] px-2 rounded-full text-[12px] font-mono ${tone === "success" ? "bg-success-tint text-success" : "bg-error-tint text-error"}`}>{t}</span>
      ))}
    </span>
  );
}

function RecentRuns({ blueprint, testId, now, onOpen }: { blueprint: string; testId: string; now: number; onOpen: (id: string) => void }) {
  const { data: runs } = useLoad(() => api.testRuns({ blueprint, test_id: testId, limit: 10 }), [blueprint, testId], 4000);
  return (
    <Card className="w-[320px] shrink-0 overflow-hidden">
      <div className="flex justify-between items-center px-4 py-3.5 border-b border-hairline">
        <span className="text-[15px] font-semibold">Recent runs</span>
        <span className="text-small text-text-secondary">this test</span>
      </div>
      {!runs && <div className="px-4 py-3 flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div>}
      {runs?.length === 0 && <p className="m-0 px-4 py-4 text-small text-text-secondary">Not run yet. Use Run now.</p>}
      {runs?.map((r) => {
        const chip = statusChip(r.status);
        const failure = r.checks.find((c) => c.outcome === "fail")?.detail ?? r.error;
        return (
          <button key={r.id} type="button" onClick={() => onOpen(r.id)}
            className={`w-full text-left flex flex-col gap-1.5 px-4 py-2.5 border-b border-hairline cursor-pointer hover:bg-container-low ${r.status === "failed" || r.status === "error" ? "bg-warning-tint/40" : ""}`}>
            <span className="flex justify-between items-start gap-2">
              <span className="flex flex-col gap-0.5 min-w-0">
                <span className="text-[13px] font-medium">{formatDateTime(r.created_at)}</span>
                <span className="text-small text-text-secondary">{r.instance_id}{r.duration_s !== null ? <> · <Mono>{r.duration_s} s</Mono></> : ` · ${timeAgo(r.created_at, now)}`}</span>
              </span>
              <Chip tone={chip.tone}>{r.status === "running" ? <span className="flex items-center gap-1"><Spinner />Running</span> : chip.label}</Chip>
            </span>
            {failure && (r.status === "failed" || r.status === "error") && <span className="text-small text-error leading-4">{failure}</span>}
          </button>
        );
      })}
    </Card>
  );
}

// Stop a run that is still going (it may never come back: the instance is gone or the agent died), or,
// for an Admin, delete a finished one. The row in Recent runs is itself a button, so the controls live here.
function RunModal({ runId, onClose, onChanged }: { runId: string; onClose: () => void; onChanged: () => void }) {
  const { can } = useAuth();
  const { data: r, error, reload } = useLoad(() => api.testRun(runId), [runId], 3000);
  const [busy, setBusy] = useState<"stop" | "delete" | null>(null);
  const [actError, setActError] = useState<string | null>(null);
  const going = r?.status === "running";
  async function act(kind: "stop" | "delete") {
    if (!r) return;
    const question = kind === "stop"
      ? `Stop this run of ${r.test_id} on ${r.instance_id}? It will be marked Cancelled and will not count as a result.`
      : `Delete this run of ${r.test_id}? Its checks and assurance claims go with it. This cannot be undone.`;
    if (!window.confirm(question)) return;
    setBusy(kind); setActError(null);
    try {
      if (kind === "stop") { await api.cancelTestRun(r.id); await reload(); onChanged(); }
      else { await api.deleteTestRun(r.id); onChanged(); onClose(); }
    } catch (e) { setActError(errorText(e)); } finally { setBusy(null); }
  }
  return (
    <Modal width={760} title={r ? `${r.test_id} on ${r.instance_id}` : "Test run"} onClose={onClose}
      subtitle={r ? `${r.blueprint} v${r.version} · ${formatDateTime(r.created_at)} · by ${r.created_by}` : undefined}
      footer={<>
        {r && going && can("tests.run") && <Button variant="danger" disabled={busy !== null} onClick={() => void act("stop")}>{busy === "stop" ? "Stopping…" : "Stop run"}</Button>}
        {r && !going && can("tests.manage") && <Button variant="danger" disabled={busy !== null} onClick={() => void act("delete")}>{busy === "delete" ? "Deleting…" : "Delete run"}</Button>}
        <Button variant="primary" onClick={onClose}>Close</Button>
      </>}>
      {error && <Banner tone="error">{error}</Banner>}
      {actError && <Banner tone="error">{actError}</Banner>}
      {!r && !error && <div className="flex gap-2 items-center text-text-secondary"><Spinner /> Loading…</div>}
      {r && <RunDetail run={r} />}
    </Modal>
  );
}

export function RunDetail({ run: r }: { run: TestRun }) {
  const chip = statusChip(r.status);
  const outcomeIcon = { pass: ["checkCircle", "text-success"], fail: ["xCircle", "text-error"], not_verifiable: ["clock", "text-text-secondary"] } as const;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2 text-small">
        <Chip tone={chip.tone}>{chip.label}</Chip>
        {r.duration_s !== null && <span className="text-text-secondary">{r.duration_s} s</span>}
        {r.usage?.total_tokens !== undefined && <span className="text-text-secondary">· {r.usage.total_tokens.toLocaleString("en-GB")} tokens</span>}
        <span className="text-text-secondary">· evidence: {r.evidence === "transcript" ? "Hermes session transcript" : "none"}</span>
      </div>
      {r.status === "running" && <Banner tone="info"><span className="flex items-center gap-2"><Spinner />Running on {r.profile} ({r.instance_id}); this updates by itself.</span></Banner>}
      {r.status === "cancelled" && <Banner tone="info">Stopped before it finished{r.error ? ` (${r.error})` : ""}. A cancelled run says nothing about the test and does not count as its result.</Banner>}
      {r.evidence_error && <Banner tone="warning">No transcript: {r.evidence_error}</Banner>}
      {r.notes.map((n) => <Banner key={n} tone="info">{n}</Banner>)}

      {r.checks.length > 0 && (
        <div>
          <div className="text-label uppercase text-text-secondary mb-2">Checks</div>
          <div className="border border-hairline rounded-control divide-y divide-hairline">
            {r.checks.map((c) => {
              const [icon, color] = outcomeIcon[c.outcome];
              return (
                <div key={c.id} className="flex gap-2.5 px-3 py-2.5 text-[13px] leading-[18px]">
                  <Icon name={icon} size={16} className={`${color} shrink-0 mt-0.5`} />
                  <div><span className="font-medium">{c.kind.replace("_", " ")}</span> <Mono>{c.subject}</Mono><br /><span className="text-text-secondary">{c.detail}</span></div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {r.claims.length > 0 && (
        <div>
          <div className="text-label uppercase text-text-secondary mb-2">Claims</div>
          <div className="flex flex-col gap-1.5">
            {r.claims.map((c, n) => (
              <div key={n} className="flex items-center justify-between gap-3 text-[13px]">
                <span>{c.claim}</span><Chip tone={VERDICT_TONE[c.verdict]}>{c.verdict}</Chip>
              </div>
            ))}
          </div>
        </div>
      )}

      {r.tool_calls && (
        <div>
          <div className="text-label uppercase text-text-secondary mb-2">Tool calls in the run ({r.tool_calls.length})</div>
          {r.tool_calls.length === 0 ? <p className="m-0 text-small text-text-secondary">The run called no tools.</p> : (
            <div className="border border-hairline rounded-control divide-y divide-hairline max-h-[200px] overflow-auto">
              {r.tool_calls.map((c, n) => (
                <div key={n} className="px-3 py-2 text-small">
                  <Mono className="font-semibold">{c.name}</Mono>{!c.answered && <span className="text-warning"> · no result</span>}
                  <div className="text-text-secondary font-mono text-[11px] break-all">{c.arguments}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {r.output && (
        <details className="text-small">
          <summary className="cursor-pointer text-text-secondary">Output</summary>
          <pre className="mt-2 mb-0 p-3 bg-container-low rounded-control whitespace-pre-wrap break-words font-mono text-[12px] max-h-[240px] overflow-auto">{r.output}</pre>
        </details>
      )}
    </div>
  );
}

function NewTestModal({ suite, onClose, onCreate }: { suite: Suite; onClose: () => void; onCreate: (test: BlueprintTest) => void }) {
  const [target, setTarget] = useState(suite.agents[0]?.id ?? "");
  const [label, setLabel] = useState("");
  const id = testIdFrom(label);
  const taken = suite.tests.some((t) => t.test.id === id);
  return (
    <Modal width={480} title="New test" subtitle={`Added to ${suite.blueprint} v${suite.version} when you save it.`} onClose={onClose}
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={!id || taken || !target} onClick={() => onCreate(newTest(target, id))}>Continue</Button>
      </>}>
      <Field label="Target agent">
        <select className={INPUT} value={target} onChange={(e) => setTarget(e.target.value)}>
          {suite.agents.map((a) => <option key={a.id} value={a.id}>{a.id}</option>)}
        </select>
      </Field>
      <Field label="What it checks" hint={id ? (taken ? `A test named ${id} already exists.` : <>Test id: <Mono>{id}</Mono></>) : "A few words, e.g. “screener cites list versions”."}>
        <input className={INPUT} autoFocus value={label} onChange={(e) => setLabel(e.target.value)} />
      </Field>
    </Modal>
  );
}
