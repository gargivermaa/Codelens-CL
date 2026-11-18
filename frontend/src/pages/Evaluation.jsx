import { useMemo, useState } from "react";
import { FlaskConical, Terminal } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "../api.js";
import { Card, Empty, ErrorState, Loading, PageHeader, SeverityBadge } from "../components/ui.jsx";
import { CATEGORICAL } from "../components/charts.jsx";

const CONFIGS = [
  { id: "rules", label: "Rules only", hint: "deterministic security rules" },
  { id: "llm", label: "LLM only", hint: "Gemini on the diff, no repository context" },
  { id: "llm_context", label: "LLM + context", hint: "CodeLens: Gemini + hybrid retrieval" },
];
const SPLITS = ["holdout", "validation", "dev", "all", "v1"];
const MODE_LABEL = { bm25: "BM25", dense: "Dense (Gemini)", hybrid: "Hybrid (RRF)" };

const pct = (v) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
const num = (v, d = 3) => (v == null ? "—" : Number(v).toFixed(d));

function Cmd({ children }) {
  return <code className="rounded bg-raised px-1.5 py-0.5 font-mono text-[11.5px] text-ink">{children}</code>;
}

function NotRun({ config, split }) {
  return <span className="text-xs text-faint">not run — <Cmd>python -m app.evaluation.run review --config {config} --split {split}</Cmd></span>;
}

function ComparisonTable({ reports, split }) {
  const rows = [
    ["Precision", "precision", pct], ["Recall", "recall", pct], ["F1", "f1", num],
    ["Recall, any category (lenient)", "lenient_recall", pct], ["False-negative rate", "false_negative_rate", pct],
    ["Clean-PR false-positive rate", "clean_case_false_positive_rate", pct],
    ["Category accuracy (of detected)", "category_accuracy", pct], ["Severity exact (of detected)", "severity_exact", pct],
    ["Severity within one level", "severity_within_one", pct], ["Exact line (of detected)", "line_exact", pct],
    ["Duplicate rate", "duplicate_rate", pct],
  ];
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <thead><tr className="border-b border-line">
          <th className="table-head py-2 pr-3">Metric (confirmed findings)</th>
          {CONFIGS.map((c) => <th key={c.id} className="table-head px-3 py-2 text-right">{c.label}</th>)}
        </tr></thead>
        <tbody>
          {rows.map(([label, key, fmt]) => (
            <tr key={key} className="border-b border-line/60 last:border-0">
              <td className="py-2 pr-3 text-muted">{label}</td>
              {CONFIGS.map((c) => {
                const r = reports[c.id];
                return <td key={c.id} className="px-3 py-2 text-right tabular-nums">{r ? fmt(r.confirmed_only?.[key]) : "—"}</td>;
              })}
            </tr>
          ))}
          <tr className="border-b border-line/60">
            <td className="py-2 pr-3 text-muted">Detected / expected defects</td>
            {CONFIGS.map((c) => { const m = reports[c.id]?.confirmed_only; return <td key={c.id} className="px-3 py-2 text-right tabular-nums">{m ? `${m.true_positives} / ${m.expected}` : "—"}</td>; })}
          </tr>
          <tr className="border-b border-line/60">
            <td className="py-2 pr-3 text-muted">Median latency per PR</td>
            {CONFIGS.map((c) => { const l = reports[c.id]?.latency; return <td key={c.id} className="px-3 py-2 text-right tabular-nums">{l?.median_ms == null ? "—" : l.median_ms < 1000 ? `${Math.round(l.median_ms)} ms` : `${(l.median_ms / 1000).toFixed(1)} s`}</td>; })}
          </tr>
          <tr className="border-b border-line/60">
            <td className="py-2 pr-3 text-muted">LLM tokens per PR (mean)</td>
            {CONFIGS.map((c) => <td key={c.id} className="px-3 py-2 text-right tabular-nums">{reports[c.id]?.tokens ? reports[c.id].tokens.per_case_mean : "—"}</td>)}
          </tr>
          <tr>
            <td className="py-2 pr-3 text-muted">API failures</td>
            {CONFIGS.map((c) => <td key={c.id} className={`px-3 py-2 text-right tabular-nums ${reports[c.id]?.api_failures ? "text-critical" : ""}`}>{reports[c.id]?.api_failures ?? "—"}</td>)}
          </tr>
        </tbody>
      </table>
      <div className="mt-3 space-y-1">
        {CONFIGS.filter((c) => !reports[c.id]).map((c) => <div key={c.id}><NotRun config={c.id} split={split} /></div>)}
      </div>
    </div>
  );
}

function PRFChart({ reports }) {
  const data = CONFIGS.filter((c) => reports[c.id]).map((c) => {
    const m = reports[c.id].confirmed_only;
    return { name: c.label, Precision: m.precision ?? 0, Recall: m.recall ?? 0, F1: m.f1 ?? 0 };
  });
  if (!data.length) return <Empty title="No review runs for this split" />;
  const axis = { stroke: "#26304a", tick: { fill: "#8f98ad", fontSize: 12 }, tickLine: false };
  return (
    <div style={{ height: 260 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }} barGap={2} barCategoryGap="22%">
          <CartesianGrid stroke="#1f2840" vertical={false} />
          <XAxis dataKey="name" {...axis} />
          <YAxis domain={[0, 1]} {...axis} axisLine={false} tickFormatter={(v) => `${v * 100}%`} />
          <Tooltip cursor={{ fill: "#ffffff08" }} formatter={(v) => `${(v * 100).toFixed(1)}%`}
            contentStyle={{ background: "#1a2235", border: "1px solid #26304a", borderRadius: 8, fontSize: 12 }} />
          <Legend wrapperStyle={{ fontSize: 12 }} formatter={(value) => <span style={{ color: "#c5cbd8" }}>{value}</span>} />
          {["Precision", "Recall", "F1"].map((key, i) => (
            <Bar key={key} dataKey={key} fill={CATEGORICAL[i]} radius={[4, 4, 0, 0]} maxBarSize={28} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function ConfusionMatrix({ matrix }) {
  if (!matrix) return <Empty title="Run a review benchmark to see the confusion matrix" />;
  const max = Math.max(1, ...matrix.rows.flatMap((r) => matrix.columns.map((c) => matrix.counts[r][c])));
  const correct = { security: "security", bug: "bug", clean: "none" };
  return (
    <div className="overflow-x-auto">
      <table className="text-sm">
        <thead><tr>
          <th className="table-head p-2">Expected ↓ / Predicted →</th>
          {matrix.columns.map((c) => <th key={c} className="table-head p-2 text-center capitalize">{c === "none" ? "nothing" : c}</th>)}
        </tr></thead>
        <tbody>{matrix.rows.map((r) => (
          <tr key={r}>
            <td className="p-2 text-xs font-medium capitalize text-muted">{r === "clean" ? "clean PR" : r}</td>
            {matrix.columns.map((c) => {
              const n = matrix.counts[r][c];
              const ok = correct[r] === c;
              return (
                <td key={c} className="p-1">
                  <div className={`flex h-12 w-20 items-center justify-center rounded-md text-sm tabular-nums ${ok ? "ring-1 ring-good/60" : ""}`}
                    style={{ background: n ? `rgba(57,135,229,${0.12 + 0.6 * (n / max)})` : "#1a2235" }}
                    title={`${r} → ${c}: ${n}`}>{n}</div>
                </td>
              );
            })}
          </tr>))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-faint">Green outline = correct outcome. Rows "security"/"bug" count expected defects; "clean PR" counts findings on clean PRs (or "nothing" once per silent clean PR).</p>
    </div>
  );
}

function PerType({ byType }) {
  if (!byType) return <Empty title="No per-type results" />;
  const rows = Object.entries(byType).sort((a, b) => b[1].expected - a[1].expected);
  return (
    <table className="w-full text-sm">
      <thead><tr className="border-b border-line">
        <th className="table-head py-2">Defect type</th><th className="table-head py-2 text-right">Cases</th>
        <th className="table-head py-2 text-right">Detected (any category)</th><th className="table-head py-2 text-right">Correct (strict)</th>
      </tr></thead>
      <tbody>{rows.map(([type, v]) => (
        <tr key={type} className="border-b border-line/60 last:border-0">
          <td className="py-2 font-mono text-xs">{type}</td>
          <td className="py-2 text-right tabular-nums">{v.expected}</td>
          <td className="py-2 text-right tabular-nums">{v.detected} <span className="text-faint">({pct(v.detection_rate)})</span></td>
          <td className="py-2 text-right tabular-nums">{v.strict} <span className="text-faint">({pct(v.recall)})</span></td>
        </tr>))}</tbody>
    </table>
  );
}

function RetrievalTable({ report }) {
  if (!report) return <Empty title="No retrieval run for this split" Icon={FlaskConical}><Cmd>python -m app.evaluation.run retrieval --split holdout</Cmd></Empty>;
  const k = report.k;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] text-sm">
        <thead><tr className="border-b border-line">
          {["Mode", "P@1", `P@${k}`, `R@${k}`, `NDCG@${k}`, "MRR", "Median latency"].map((h) => <th key={h} className="table-head px-3 py-2 first:pl-0">{h}</th>)}
        </tr></thead>
        <tbody>{Object.entries(report.modes || {}).map(([mode, m]) => (
          <tr key={mode} className="border-b border-line/60 last:border-0">
            <td className="py-2 pr-3 font-medium">{MODE_LABEL[mode] || mode}</td>
            {m.skipped ? <td colSpan={6} className="px-3 py-2 text-xs text-muted">skipped: {m.skipped}</td> : (<>
              <td className="px-3 py-2 tabular-nums">{num(m["precision@1"])}</td>
              <td className="px-3 py-2 tabular-nums">{num(m[`precision@${k}`])}</td>
              <td className="px-3 py-2 tabular-nums">{num(m[`recall@${k}`])}</td>
              <td className="px-3 py-2 tabular-nums">{num(m[`ndcg@${k}`])}</td>
              <td className="px-3 py-2 tabular-nums">{num(m.mrr)}</td>
              <td className="px-3 py-2 tabular-nums">{m.latency?.median_ms != null ? `${m.latency.median_ms} ms` : "—"}</td>
            </>)}
          </tr>))}</tbody>
      </table>
      <p className="mt-2 text-xs text-faint">{report.queries} queries · {report.corpus_chunks} chunks · RRF k={report.params?.rrf_k ?? 60}, weights (dense, BM25) = {(report.params?.weights_dense_bm25 || [1, 1]).join(", ")} · {report.file}</p>
    </div>
  );
}

function CaseInspector({ runId }) {
  const { data, error, loading } = useApi(runId ? `/api/benchmarks/runs/${runId}` : null);
  const [selected, setSelected] = useState(null);
  if (!runId) return <Empty title="Select a configuration with a run" />;
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} />;
  const cases = (data.cases || []).filter((c) => (c.repeat ?? 0) === 0);
  const current = cases.find((c) => c.id === selected) || cases[0];
  const outcome = (c) => {
    const confirmed = c.predicted.filter((p) => p.status === "confirmed");
    if (!c.expected.length) return confirmed.length ? ["false alarm", "text-critical"] : ["clean ✓", "text-good"];
    const hit = c.expected.every((e) => confirmed.some((p) => p.file === e.file && p.start_line - 2 <= e.line && e.line <= p.end_line + 2));
    return hit ? ["detected", "text-good"] : ["missed", "text-warning"];
  };
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[260px_1fr]">
      <ul className="max-h-[520px] space-y-1 overflow-y-auto pr-1">
        {cases.map((c) => {
          const [label, tone] = outcome(c);
          return (
            <li key={c.id}>
              <button onClick={() => setSelected(c.id)}
                className={`focus-ring flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-xs ${current?.id === c.id ? "bg-accent/15 text-ink" : "text-muted hover:bg-raised"}`}>
                <span className="font-mono">{c.id}</span>
                <span className="truncate px-2 text-faint">{c.expected[0]?.type || "clean"}</span>
                <span className={tone}>{label}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {current && (
        <div className="space-y-4 text-sm">
          <div>
            <div className="text-[11px] font-medium uppercase tracking-wide text-faint">Ground truth</div>
            {current.expected.length ? current.expected.map((e, i) => (
              <div key={i} className="mt-1 flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-accent2">{e.file}:{e.line}</span>
                <span className="rounded bg-raised px-1.5 py-0.5 text-xs">{e.type}</span>
                <span className="text-xs capitalize text-muted">{e.category}</span>
                {e.severity && <SeverityBadge severity={e.severity} />}
              </div>
            )) : <div className="mt-1 text-xs text-muted">Clean PR: no defect expected.</div>}
          </div>
          <div>
            <div className="text-[11px] font-medium uppercase tracking-wide text-faint">Findings after validation</div>
            {current.predicted.length ? (
              <table className="mt-1 w-full text-xs">
                <tbody>{current.predicted.map((p, i) => (
                  <tr key={i} className="border-b border-line/60 last:border-0">
                    <td className="py-1.5 pr-2 font-mono">{p.file.split("/").pop()}:{p.start_line}{p.end_line !== p.start_line ? `-${p.end_line}` : ""}</td>
                    <td className="py-1.5 pr-2 capitalize">{p.category}</td>
                    <td className="py-1.5 pr-2"><SeverityBadge severity={p.severity} /></td>
                    <td className={`py-1.5 pr-2 ${p.status === "confirmed" ? "text-good" : "text-warning"}`}>{p.status}</td>
                    <td className="py-1.5 pr-2 text-muted">{p.title}</td>
                    <td className="py-1.5 text-faint">{p.source}</td>
                  </tr>))}</tbody>
              </table>
            ) : <div className="mt-1 text-xs text-muted">No findings.</div>}
          </div>
          {current.dropped?.length > 0 && (
            <div>
              <div className="text-[11px] font-medium uppercase tracking-wide text-faint">Dropped by validation</div>
              <ul className="mt-1 list-disc pl-5 text-xs text-muted">{current.dropped.map((d, i) => <li key={i}>{d.title}: {d.reason}</li>)}</ul>
            </div>
          )}
          {current.errors?.length > 0 && (
            <div>
              <div className="text-[11px] font-medium uppercase tracking-wide text-faint">Pipeline notes</div>
              <ul className="mt-1 list-disc pl-5 text-xs text-muted">{current.errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
            </div>
          )}
          {current.raw_model_output?.some(Boolean) && (
            <details className="rounded-md border border-line">
              <summary className="cursor-pointer px-3 py-2 text-xs text-muted">Raw model output ({current.raw_model_output.length} LLM calls)</summary>
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap border-t border-line p-3 font-mono text-[11.5px] text-ink">{current.raw_model_output.map((r) => (r || []).join("\n---\n")).join("\n\n=====\n\n")}</pre>
            </details>
          )}
          <div className="text-xs text-faint">Latency {current.latency_ms} ms · risk {current.risk} · API failures {current.api_failures ?? 0}</div>
        </div>
      )}
    </div>
  );
}

export default function Evaluation() {
  const { data, error, loading, reload } = useApi("/api/benchmarks");
  const [split, setSplit] = useState("holdout");
  const [config, setConfig] = useState("llm_context");
  const reports = data?.reports || {};
  const byConfig = useMemo(() => Object.fromEntries(CONFIGS.map((c) => [c.id, reports[`review:${c.id}:${split}`]]).filter(([, v]) => v)), [reports, split]);
  const selected = byConfig[config] || Object.values(byConfig)[0];
  const sweep = reports["retrieval-sweep:validation"];

  return (
    <>
      <PageHeader title="Evaluation" subtitle="Measured benchmark runs: does repository context improve AI code review?"
        actions={
          <select value={split} onChange={(e) => setSplit(e.target.value)} aria-label="Split"
            className="focus-ring rounded-md border border-line bg-surface px-2 py-1.5 text-xs text-ink">
            {SPLITS.map((s) => <option key={s} value={s}>{s === "v1" ? "v1 (earlier runs)" : `${s} split`}</option>)}
          </select>} />
      {loading && !data ? <Loading /> : error ? <ErrorState error={error} onRetry={reload} /> : (
        <div className="space-y-6">
          <div className="card flex flex-wrap items-start gap-3 p-4 text-xs leading-relaxed text-muted">
            <Terminal className="mt-0.5 h-4 w-4 shrink-0 text-accent" aria-hidden />
            <div>
              Headline numbers use the <b className="text-ink">holdout</b> split, which was never used for tuning. Strict scoring: a
              finding counts as correct only in the right file, within ±2 lines, <i>and</i> with the right category; "lenient" ignores the category.
              Reproduce: <Cmd>python -m app.evaluation.run review --config all --split holdout</Cmd>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.4fr_1fr]">
            <Card title="Rules vs LLM vs LLM + repository context" subtitle={`Review benchmark · ${split} split · confirmed findings`}>
              <ComparisonTable reports={byConfig} split={split} />
            </Card>
            <Card title="Precision, recall and F1" subtitle="Same cases and model for every configuration">
              <PRFChart reports={byConfig} />
            </Card>
          </div>

          <Card title="Detail for one configuration" subtitle={selected ? `${selected.run_id} · model ${selected.environment?.review_model || "none"} · prompt ${selected.environment?.prompt_version} · dataset ${selected.dataset_version}` : "no run"}
            action={
              <div className="flex gap-1">
                {CONFIGS.map((c) => (
                  <button key={c.id} disabled={!byConfig[c.id]} onClick={() => setConfig(c.id)}
                    className={`focus-ring rounded-md border px-2.5 py-1 text-xs disabled:opacity-40 ${config === c.id && byConfig[c.id] ? "border-accent bg-accent/15 text-ink" : "border-line text-muted hover:text-ink"}`}>{c.label}</button>
                ))}
              </div>}>
            {!selected ? <Empty title="No review runs for this split" Icon={FlaskConical} /> : (
              <div className="space-y-8">
                {selected.variability && (
                  <div className="text-xs text-muted">Repeated {selected.repeats}×: F1 mean {num(selected.variability.f1_mean)} ± {num(selected.variability.f1_stdev)} (std. dev.)</div>
                )}
                <div className="grid grid-cols-1 gap-8 xl:grid-cols-2">
                  <div><div className="mb-2 text-sm font-medium">Confusion matrix (category groups)</div><ConfusionMatrix matrix={selected.confirmed_only?.confusion_matrix} /></div>
                  <div><div className="mb-2 text-sm font-medium">Recall per defect type</div><PerType byType={selected.confirmed_only?.by_type} /></div>
                </div>
                <div><div className="mb-3 text-sm font-medium">Case inspection</div><CaseInspector runId={selected.run_id} /></div>
              </div>
            )}
          </Card>

          <Card title="Retrieval: BM25 vs dense vs hybrid" subtitle={`${split} split`}>
            <RetrievalTable report={reports[`retrieval:${split}`]} />
          </Card>

          {sweep && (
            <Card title="RRF parameter sweep (validation split only)" subtitle={sweep.note}>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[480px] text-sm">
                  <thead><tr className="border-b border-line">{["rrf_k", "weights (dense, BM25)", "candidates", "NDCG@5", "MRR", "P@1"].map((h) => <th key={h} className="table-head px-3 py-2 first:pl-0">{h}</th>)}</tr></thead>
                  <tbody>{sweep.results.slice(0, 8).map((r, i) => (
                    <tr key={i} className="border-b border-line/60 last:border-0">
                      <td className="py-2 pr-3 tabular-nums">{r.rrf_k}</td><td className="px-3 py-2 tabular-nums">{r.weights_dense_bm25.join(", ")}</td>
                      <td className="px-3 py-2 tabular-nums">{r.candidate_k}</td><td className="px-3 py-2 tabular-nums">{num(r["ndcg@5"])}</td>
                      <td className="px-3 py-2 tabular-nums">{num(r.mrr)}</td><td className="px-3 py-2 tabular-nums">{num(r["precision@1"])}</td>
                    </tr>))}</tbody>
                </table>
              </div>
            </Card>
          )}
        </div>
      )}
    </>
  );
}
