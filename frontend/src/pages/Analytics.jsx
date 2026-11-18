import { useState } from "react";
import { Link } from "react-router-dom";
import { FlaskConical } from "lucide-react";
import { useApi } from "../api.js";
import { Card, Empty, ErrorState, Loading, PageHeader, StatCard } from "../components/ui.jsx";
import { CATEGORICAL, DailyBars, HistoryChart, HorizontalBars, RiskDistributionChart } from "../components/charts.jsx";

const duration = (ms) => (ms == null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);


export default function Analytics() {
  const [days, setDays] = useState(30);
  const { data, error, loading, reload } = useApi(`/api/analytics?days=${days}`);

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

          <Card title="Evaluation benchmarks">
            <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted">
              <span>Measured precision / recall / F1 for rules vs LLM vs LLM + repository context, retrieval comparison and per-case inspection.</span>
              <Link to="/evaluation" className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-xs text-ink hover:bg-raised">
                <FlaskConical className="h-3.5 w-3.5" aria-hidden />Open evaluation
              </Link>
            </div>
          </Card>
        </div>
      )}
    </>
  );
}
