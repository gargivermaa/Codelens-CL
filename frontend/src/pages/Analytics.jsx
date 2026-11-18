import { useState } from "react";
import { FlaskConical } from "lucide-react";
import { useApi } from "../api.js";
import { Card, Empty, ErrorState, Loading, PageHeader, StatCard } from "../components/ui.jsx";
import { CATEGORICAL, DailyBars, HistoryChart, HorizontalBars, RiskDistributionChart } from "../components/charts.jsx";

const MODE_LABEL = { bm25: "BM25", dense: "Dense", hybrid: "Hybrid (RRF)" };
const duration = (ms) => (ms == null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);

const pct = (v) => (v == null ? "—" : `${Math.round(v * 1000) / 10}%`);
const num = (v) => (v == null ? "—" : v.toFixed(3));

function RetrievalTable({ report }) {
  const k = report.k;
  const modes = Object.entries(report.modes || {});
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] text-sm">
        <thead><tr className="border-b border-line">
          {["Mode", "P@1", "P@3", `P@${k}`, `R@${k}`, "MRR", "Median latency"].map((h) => <th key={h} className="table-head px-3 py-2 first:pl-0">{h}</th>)}
        </tr></thead>
        <tbody>{modes.map(([mode, m]) => (
          <tr key={mode} className="border-b border-line/60 last:border-0">
            <td className="py-2 pr-3 font-medium">{MODE_LABEL[mode] || mode}</td>
            {m.skipped ? <td colSpan={6} className="px-3 py-2 text-xs text-muted">skipped: {m.skipped}</td> : (<>
              <td className="px-3 py-2 tabular-nums">{num(m["precision@1"])}</td>
              <td className="px-3 py-2 tabular-nums">{num(m["precision@3"])}</td>
              <td className="px-3 py-2 tabular-nums">{num(m[`precision@${k}`])}</td>
              <td className="px-3 py-2 tabular-nums">{num(m[`recall@${k}`])}</td>
              <td className="px-3 py-2 tabular-nums">{num(m.mrr)}</td>
              <td className="px-3 py-2 tabular-nums">{m.latency?.median_ms != null ? `${m.latency.median_ms} ms` : "—"}</td>
            </>)}
          </tr>))}</tbody>
      </table>
    </div>
  );
}

function ReviewBenchmark({ report, label }) {
  const c = report.confirmed_only || {};
  const llm = report.mode === "llm";
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-sm font-medium">{label}</div>
        <div className="text-xs text-muted">{llm ? `model ${report.environment?.review_model} · ` : ""}{report.file}</div>
      </div>
      {llm && report.cases_with_errors > 0 && (
        <div className="mb-3 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-ink">
          {report.cases_with_errors} case(s) had pipeline errors — this run does not measure the LLM fully.
        </div>
      )}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div><div className="text-xs text-muted">Precision</div><div className="text-xl font-semibold tabular-nums">{pct(c.finding_precision)}</div></div>
        <div><div className="text-xs text-muted">Recall</div><div className="text-xl font-semibold tabular-nums">{pct(c.finding_recall)}</div></div>
        <div><div className="text-xs text-muted">Clean-PR false positives</div><div className="text-xl font-semibold tabular-nums">{pct(c.clean_case_false_positive_rate)}</div></div>
        <div><div className="text-xs text-muted">Detected / seeded</div><div className="text-xl font-semibold tabular-nums">{c.true_positives ?? "—"} / {c.expected ?? "—"}</div></div>
      </div>
    </div>
  );
}

export default function Analytics() {
  const [days, setDays] = useState(30);
  const { data, error, loading, reload } = useApi(`/api/analytics?days=${days}`);
  const bench = useApi("/api/benchmarks");
  const reports = bench.data?.reports || {};

  return (
    <>
      <PageHeader title="Analytics" subtitle="Review activity, finding categories and measured evaluation results."
        actions={
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Time range"
            className="focus-ring rounded-md border border-line bg-surface px-2 py-1.5 text-xs text-ink">
            {[7, 30, 90, 365].map((d) => <option key={d} value={d}>Last {d} days</option>)}
          </select>} />
      {loading && !data ? <Loading /> : error ? <ErrorState error={error} onRetry={reload} /> : (
        <div className="space-y-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard label="Reviews" value={data.review_history.reduce((a, d) => a + d.reviews, 0)} hint={`last ${days} days`} />
            <StatCard label="Confirmed findings" value={data.finding_status.confirmed} hint={`${data.finding_status.possible} possible concerns`} />
            <StatCard label="Rule vs LLM findings" value={`${data.finding_sources.rule || 0} / ${data.finding_sources.llm || 0}`} hint="deterministic rules / Gemini" />
            <StatCard label="Median review time" value={duration(data.latency_ms.median)} hint={`${data.latency_ms.n} timed reviews`} />
          </div>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <Card title="Reviews per day">
              {data.review_history.length ? <DailyBars data={data.review_history} dataKey="reviews" name="Reviews" /> : <Empty title="No reviews in this period" />}
            </Card>
            <Card title="Average risk score per day" subtitle="0 = no risk, 1 = maximum">
              {data.review_history.length ? <HistoryChart data={data.review_history} dataKey="avg_risk" name="Avg risk" color={CATEGORICAL[1]} format={(v) => v?.toFixed(2)} /> : <Empty title="No reviews in this period" />}
            </Card>
            <Card title="Risk distribution" subtitle={`All reviews in the last ${days} days`}>
              <RiskDistributionChart distribution={data.risk_distribution} />
            </Card>
            <Card title="Findings by category">
              {data.categories.length ? <HorizontalBars data={data.categories} /> : <Empty title="No findings yet" />}
            </Card>
          </div>

          <Card title="Repositories" bodyClass="p-0">
            {!data.repositories.length ? <Empty title="No reviewed repositories" /> : (
              <div className="overflow-x-auto"><table className="w-full min-w-[520px] text-sm">
                <thead><tr className="border-b border-line">
                  <th className="table-head px-5 py-3">Repository</th><th className="table-head px-3 py-3 text-right">Reviews</th>
                  <th className="table-head px-3 py-3 text-right">Confirmed findings</th><th className="table-head px-5 py-3 text-right">Average risk</th>
                </tr></thead>
                <tbody>{data.repositories.map((r) => (
                  <tr key={r.full_name} className="border-b border-line/60 last:border-0">
                    <td className="px-5 py-3 font-mono text-xs">{r.full_name}</td>
                    <td className="px-3 py-3 text-right tabular-nums">{r.reviews}</td>
                    <td className="px-3 py-3 text-right tabular-nums">{r.findings}</td>
                    <td className="px-5 py-3 text-right tabular-nums">{r.avg_risk.toFixed(2)}</td>
                  </tr>))}</tbody>
              </table></div>
            )}
          </Card>

          <Card title="Evaluation benchmarks" subtitle="Measured runs from benchmarks/results (python -m app.evaluation.run …)">
            {bench.loading ? <Loading /> : bench.error ? <ErrorState error={bench.error} onRetry={bench.reload} /> :
              !Object.keys(reports).length ? (
                <Empty title="No benchmark reports yet" Icon={FlaskConical}>Run <code>python -m app.evaluation.run retrieval</code> and <code>python -m app.evaluation.run review --mode llm</code>.</Empty>
              ) : (
                <div className="space-y-8">
                  {reports.retrieval && (<div>
                    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                      <div className="text-sm font-medium">Retrieval — {reports.retrieval.queries} labelled queries, {reports.retrieval.corpus_chunks} chunks</div>
                      <div className="text-xs text-muted">{reports.retrieval.file}</div>
                    </div>
                    <RetrievalTable report={reports.retrieval} />
                  </div>)}
                  {reports["review-llm"] && <ReviewBenchmark report={reports["review-llm"]} label="Review — LLM + hybrid context" />}
                  {reports["review-rules"] && <ReviewBenchmark report={reports["review-rules"]} label="Review — deterministic rules only" />}
                </div>
              )}
          </Card>
        </div>
      )}
    </>
  );
}
