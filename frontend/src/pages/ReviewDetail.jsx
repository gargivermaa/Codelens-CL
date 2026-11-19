import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ExternalLink, FileCode2, GitCommitHorizontal, Info, ListChecks, ShieldCheck } from "lucide-react";
import { formatDate, shortSha, useApi } from "../api.js";
import { Card, Code, Empty, ErrorState, Loading, PageHeader, RISK, RiskBadge, SEVERITY, SEVERITY_ORDER,
  SeverityBadge, StatusPill } from "../components/ui.jsx";
import DiffViewer from "../components/DiffViewer.jsx";

const TABS = [
  { id: "findings", label: "Findings", Icon: ListChecks },
  { id: "diff", label: "Code diff", Icon: FileCode2 },
  { id: "details", label: "Risk & pipeline", Icon: Info },
];

function FindingCard({ finding, context }) {
  return (
    <article className="card p-5" style={{ borderLeft: `3px solid ${SEVERITY[finding.severity]?.color}` }}>
      <div className="flex flex-wrap items-center gap-2">
        <SeverityBadge severity={finding.severity} />
        <span className="rounded-md bg-raised px-2 py-0.5 text-xs capitalize text-muted">{finding.category}</span>
        <span className={`rounded-md px-2 py-0.5 text-xs ${finding.status === "confirmed" ? "bg-good/10 text-good" : "bg-warning/10 text-warning"}`}>
          {finding.status === "confirmed" ? "Confirmed" : "Possible concern"}
        </span>
        <span className="ml-auto text-xs text-faint">confidence {finding.confidence.toFixed(2)} · {finding.source}</span>
      </div>
      <h3 className="mt-3 text-base font-semibold text-ink">{finding.title}</h3>
      <div className="mt-1 font-mono text-xs text-accent2">
        {finding.file}:{finding.start_line}{finding.end_line !== finding.start_line ? `-${finding.end_line}` : ""}
      </div>
      <p className="mt-3 text-sm leading-relaxed text-muted">{finding.description}</p>
      {finding.evidence && (
        <div className="mt-4">
          <div className="text-[11px] font-medium uppercase tracking-wide text-faint">Code</div>
          <pre className="mt-1 overflow-x-auto rounded-md border border-critical/25 bg-critical/5 p-3 font-mono text-[12.5px] text-ink">{finding.evidence}</pre>
        </div>
      )}
      {finding.explanation && (
        <div className="mt-4">
          <div className="text-[11px] font-medium uppercase tracking-wide text-faint">Why it matters</div>
          <p className="mt-1 text-sm leading-relaxed text-ink/90">{finding.explanation}</p>
        </div>
      )}
      {finding.suggested_fix && (
        <div className="mt-4">
          <div className="text-[11px] font-medium uppercase tracking-wide text-faint">Suggested fix</div>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded-md border border-good/25 bg-good/5 p-3 font-mono text-[12.5px] text-ink">{finding.suggested_fix}</pre>
        </div>
      )}
      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        {finding.context_refs?.length > 0 && (
          <span>Related repository code: {finding.context_refs.map((ref) => context[ref] && <Code key={ref}>{context[ref]}</Code>)}</span>
        )}
        <span>{finding.posted_inline ? "Posted as an inline comment on GitHub" : finding.comment_line ? "Inline comment (not posted yet)" : "Listed in the review summary"}</span>
      </div>
    </article>
  );
}

function Findings({ data }) {
  const [severity, setSeverity] = useState("");
  const [status, setStatus] = useState("");
  const findings = useMemo(() => data.findings
    .filter((f) => (!severity || f.severity === severity) && (!status || f.status === status))
    .sort((a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity)), [data, severity, status]);
  if (!data.findings.length) {
    return <div className="card"><Empty title="No issues found" Icon={ShieldCheck}>The pipeline found nothing worth reporting in the reviewed changes.</Empty></div>;
  }
  const chip = (active) => `focus-ring rounded-md border px-2.5 py-1 text-xs ${active ? "border-accent bg-accent/15 text-ink" : "border-line text-muted hover:text-ink"}`;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <button className={chip(!severity)} onClick={() => setSeverity("")}>All severities</button>
        {SEVERITY_ORDER.filter((s) => data.review.severity_counts[s]).map((s) => (
          <button key={s} className={chip(severity === s)} onClick={() => setSeverity(s)}>
            {SEVERITY[s].label} ({data.review.severity_counts[s]})
          </button>
        ))}
        <span className="mx-1 w-px bg-line" />
        {[["", "Any status"], ["confirmed", "Confirmed"], ["possible", "Possible"]].map(([v, l]) => (
          <button key={l} className={chip(status === v)} onClick={() => setStatus(v)}>{l}</button>
        ))}
      </div>
      {findings.map((f) => <FindingCard key={f.id} finding={f} context={data.context} />)}
      {!findings.length && <div className="card"><Empty title="No findings match the filters" /></div>}
    </div>
  );
}

function Details({ data }) {
  const b = data.review.risk_breakdown;
  const timings = Object.entries(data.timings_ms || {});
  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
      <Card title="How the risk score was computed" subtitle={b?.formula}>
        {!b ? <Empty title="Not scored" /> : (
          <dl className="grid grid-cols-2 gap-y-3 text-sm">
            <dt className="text-muted">Findings points</dt><dd className="text-right tabular-nums">{b.findings_points}</dd>
            <dt className="text-muted">Change size points ({b.changed_lines} lines)</dt><dd className="text-right tabular-nums">{b.size_points}</dd>
            <dt className="text-muted">Sensitive surface points</dt><dd className="text-right tabular-nums">{b.surface_points}</dd>
            <dt className="text-muted">Raw total</dt><dd className="text-right tabular-nums">{b.raw}</dd>
            <dt className="font-medium text-ink">Risk score</dt><dd className="text-right font-medium tabular-nums">{b.score}</dd>
            {b.floor_applied && (<><dt className="text-muted">Severity floor applied</dt><dd className="text-right capitalize">{b.floor_applied}</dd></>)}
          </dl>
        )}
      </Card>
      <Card title="Pipeline run">
        <dl className="grid grid-cols-2 gap-y-2 text-sm">
          <dt className="text-muted">Model</dt><dd className="text-right font-mono text-xs">{data.review.model || "none (rules only)"}</dd>
          <dt className="text-muted">Retrieval</dt><dd className="text-right">{data.review.retrieval_mode || "—"} · {data.retrieval_queries} queries</dd>
          <dt className="text-muted">LLM tokens</dt><dd className="text-right tabular-nums">{data.review.total_tokens ?? "not reported"}</dd>
          <dt className="text-muted">Total time</dt><dd className="text-right tabular-nums">{data.review.total_latency_ms != null ? `${(data.review.total_latency_ms / 1000).toFixed(1)} s` : "—"}</dd>
          <dt className="text-muted">Attempts</dt><dd className="text-right tabular-nums">{data.review.attempts}</dd>
        </dl>
        {data.llm_calls.length > 0 && (
          <table className="mt-4 w-full text-xs">
            <thead><tr className="border-b border-line"><th className="table-head py-2">LLM pass</th><th className="table-head py-2">Result</th><th className="table-head py-2 text-right">Findings</th><th className="table-head py-2 text-right">Time</th></tr></thead>
            <tbody>{data.llm_calls.map((c, i) => (
              <tr key={i} className="border-b border-line/60 last:border-0">
                <td className="py-2 capitalize">{c.focus}</td>
                <td className={`py-2 ${c.ok ? "text-good" : "text-critical"}`}>{c.ok ? "ok" : c.error}</td>
                <td className="py-2 text-right tabular-nums">{c.findings ?? "—"}</td>
                <td className="py-2 text-right tabular-nums">{c.latency_ms != null ? `${(c.latency_ms / 1000).toFixed(1)} s` : "—"}</td>
              </tr>))}</tbody>
          </table>
        )}
        {timings.length > 0 && (
          <div className="mt-4">
            <div className="text-[11px] font-medium uppercase tracking-wide text-faint">LangGraph node timings</div>
            <ul className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
              {timings.map(([node, ms]) => <li key={node} className="flex justify-between"><span className="text-muted">{node}</span><span className="tabular-nums">{ms} ms</span></li>)}
            </ul>
          </div>
        )}
      </Card>
      {(data.notes.length > 0 || data.dropped_findings.length > 0) && (
        <Card title="Review notes" className="xl:col-span-2">
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted">
            {data.notes.map((n, i) => <li key={`n${i}`}>{n}</li>)}
            {data.dropped_findings.map((d, i) => <li key={`d${i}`}>Dropped “{d.title}”: {d.reason}</li>)}
          </ul>
        </Card>
      )}
    </div>
  );
}

export default function ReviewDetail() {
  const { reviewId } = useParams();
  const { data, error, loading, reload } = useApi(`/api/reviews/${reviewId}`);
  const [tab, setTab] = useState("findings");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const { review, pull_request: pr, repository: repo } = data;
  const risk = RISK[review.risk_label];
  const confirmed = data.findings.filter((f) => f.status === "confirmed").length;

  return (
    <>
      <Link to={`/pulls/${pr.id}`} className="mb-4 inline-flex items-center gap-1 text-xs text-muted hover:text-ink"><ArrowLeft className="h-3.5 w-3.5" />Review history for #{pr.number}</Link>
      <PageHeader title={<span><span className="font-mono text-muted">#{pr.number}</span> {pr.title}</span>}
        subtitle={<span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
          <Link to={`/repositories/${repo.github_id}`} className="hover:text-ink">{repo.full_name}</Link>
          <span>by {pr.author || "unknown"}</span>
          <span className="inline-flex items-center gap-1"><GitCommitHorizontal className="h-3.5 w-3.5" /><span className="font-mono">{shortSha(review.head_sha)}</span></span>
          <span>{formatDate(review.created_at)}</span>
        </span>}
        actions={<a href={pr.html_url} target="_blank" rel="noreferrer" className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-xs hover:bg-raised">Open on GitHub <ExternalLink className="h-3.5 w-3.5" /></a>} />

      <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-[1.2fr_2fr]">
        <div className="card flex items-center gap-5 p-5">
          <div className="flex h-20 w-20 shrink-0 flex-col items-center justify-center rounded-full border-4"
            style={{ borderColor: risk?.color || "#26304a" }}>
            <span className="text-xl font-semibold tabular-nums">{review.risk_score != null ? Math.round(review.risk_score * 100) : "—"}</span>
            <span className="text-[10px] uppercase tracking-wide text-muted">risk</span>
          </div>
          <div className="space-y-2">
            <RiskBadge label={review.risk_label} size="lg" />
            <div><StatusPill status={review.status} /></div>
            <div className="text-xs text-muted">{review.posted ? "Review posted to GitHub" : "Not posted to GitHub"}</div>
          </div>
        </div>
        <div className="card grid grid-cols-3 gap-4 p-5 sm:grid-cols-5">
          {SEVERITY_ORDER.map((s) => (
            <div key={s}>
              <div className="flex items-center gap-1.5 text-xs text-muted"><span className="h-2 w-2 rounded-full" style={{ background: SEVERITY[s].color }} />{SEVERITY[s].label}</div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">{review.severity_counts[s] || 0}</div>
            </div>
          ))}
          <div className="col-span-3 text-xs text-muted sm:col-span-5">
            {confirmed} confirmed · {data.findings.length - confirmed} possible · {review.files_reviewed ?? 0} file(s) reviewed
          </div>
        </div>
      </div>

      {review.error && (
        <div className="mb-6 rounded-lg border border-critical/40 bg-critical/10 px-4 py-3 text-sm text-ink" role="alert">
          <span className="font-medium text-critical">Review error:</span> {review.error}
        </div>
      )}

      <div className="mb-4 flex gap-1 border-b border-line" role="tablist">
        {TABS.map(({ id, label, Icon }) => (
          <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
            className={`focus-ring -mb-px inline-flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm ${tab === id ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink"}`}>
            <Icon className="h-4 w-4" aria-hidden />{label}
            {id === "findings" && <span className="rounded bg-raised px-1.5 text-xs tabular-nums">{data.findings.length}</span>}
            {id === "diff" && <span className="rounded bg-raised px-1.5 text-xs tabular-nums">{data.files.length}</span>}
          </button>
        ))}
      </div>
      {tab === "findings" && <Findings data={data} />}
      {tab === "diff" && <DiffViewer files={data.files} findings={data.findings} />}
      {tab === "details" && <Details data={data} />}
    </>
  );
}
