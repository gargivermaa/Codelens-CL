import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { formatDate, shortSha, useApi } from "../api.js";
import { Card, Empty, ErrorState, Loading, PageHeader, StatCard } from "../components/ui.jsx";
import PullTable from "../components/PullTable.jsx";

export default function RepositoryDetail() {
  const { githubId } = useParams();
  const { data, error, loading, reload } = useApi(`/api/repositories/${githubId}`);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const { repository: repo, pull_requests: pulls } = data;
  const reviewed = pulls.filter((p) => p.latest_review);
  const highRisk = reviewed.filter((p) => ["high", "critical"].includes(p.latest_review.risk_label)).length;
  return (
    <>
      <Link to="/repositories" className="mb-4 inline-flex items-center gap-1 text-xs text-muted hover:text-ink"><ArrowLeft className="h-3.5 w-3.5" />Repositories</Link>
      <PageHeader title={repo.full_name}
        subtitle={`Default branch ${repo.default_branch || "unknown"} · installation ${repo.installation_id ?? "—"}`}
        actions={<a href={repo.html_url} target="_blank" rel="noreferrer" className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-xs hover:bg-raised">GitHub <ExternalLink className="h-3.5 w-3.5" /></a>} />
      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Indexed chunks" value={repo.indexed_chunks ?? "—"}
          hint={repo.indexed ? `at ${shortSha(repo.indexed_sha)} · ${formatDate(repo.indexed_at)}` : "Indexed on the first pull request"} />
        <StatCard label="Pull requests" value={pulls.length} hint={`${reviewed.length} reviewed`} />
        <StatCard label="High-risk PRs" value={highRisk} tone="text-serious" />
      </div>
      <Card title="Review history" bodyClass="p-0">
        {pulls.length ? <PullTable pulls={pulls} showRepo={false} /> : <Empty title="No pull requests reviewed yet" />}
      </Card>
    </>
  );
}
