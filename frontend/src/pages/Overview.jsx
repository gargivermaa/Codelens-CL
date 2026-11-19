import { Link } from "react-router-dom";
import { AlertOctagon, FolderGit2, GitPullRequest, ListChecks } from "lucide-react";
import { useApi } from "../api.js";
import { Card, Empty, ErrorState, Loading, PageHeader, SEVERITY, SEVERITY_ORDER, StatCard } from "../components/ui.jsx";
import { RiskDistributionChart } from "../components/charts.jsx";
import PullTable from "../components/PullTable.jsx";

export default function Overview() {
  const { data, error, loading, reload } = useApi("/api/overview");
  return (
    <>
      <PageHeader title="Overview" subtitle="Pull requests reviewed by CodeLens CI, with repository-aware context." />
      {loading && !data ? <Loading /> : error ? <ErrorState error={error} onRetry={reload} /> : (
        <div className="space-y-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard label="Repositories" value={data.repositories} Icon={FolderGit2} hint="Connected via the GitHub App" />
            <StatCard label="PRs reviewed" value={data.pull_requests_reviewed} Icon={GitPullRequest}
              hint={`${data.reviews_total} review run${data.reviews_total === 1 ? "" : "s"} in total`} />
            <StatCard label="High-risk PRs" value={data.high_risk_pull_requests} Icon={AlertOctagon} tone="text-serious"
              hint="Latest review rated high or critical" />
            <StatCard label="Findings" value={data.findings_total} Icon={ListChecks}
              hint={`${data.findings_confirmed} confirmed · ${data.findings_total - data.findings_confirmed} possible`} />
          </div>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
            <Card title="Risk distribution" subtitle="Latest review of each pull request" className="xl:col-span-2">
              {data.pull_requests_reviewed ? <RiskDistributionChart distribution={data.risk_distribution} />
                : <Empty title="No reviews yet">Open a pull request in a repository where the GitHub App is installed.</Empty>}
            </Card>
            <Card title="Findings by severity" subtitle="All stored findings">
              <ul className="space-y-3">
                {SEVERITY_ORDER.map((s) => {
                  const n = data.findings_by_severity[s] || 0;
                  const pct = data.findings_total ? (n / data.findings_total) * 100 : 0;
                  return (
                    <li key={s}>
                      <div className="mb-1 flex justify-between text-xs"><span className="text-muted">{SEVERITY[s].label}</span><span className="tabular-nums text-ink">{n}</span></div>
                      <div className="h-1.5 rounded-full bg-raised"><div className="h-1.5 rounded-full" style={{ width: `${pct}%`, background: SEVERITY[s].color }} /></div>
                    </li>
                  );
                })}
              </ul>
            </Card>
          </div>

          <Card title="Recent pull requests" subtitle="Click a row to open its review"
            action={<Link to="/pulls" className="text-xs text-accent hover:underline">View all</Link>} bodyClass="p-0">
            {data.recent_pull_requests.length ? <PullTable pulls={data.recent_pull_requests} />
              : <Empty title="No pull requests yet">Reviews appear here as soon as the webhook delivers a pull request event.</Empty>}
          </Card>
        </div>
      )}
    </>
  );
}
