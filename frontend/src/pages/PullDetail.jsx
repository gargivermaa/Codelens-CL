import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { formatDate, shortSha, useApi } from "../api.js";
import { Card, Empty, ErrorState, Loading, PageHeader, RiskBadge, SeverityCounts, StatusPill } from "../components/ui.jsx";

export default function PullDetail() {
  const { prId } = useParams();
  const { data, error, loading, reload } = useApi(`/api/pulls/${prId}`);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  return (
    <>
      <Link to="/pulls" className="mb-4 inline-flex items-center gap-1 text-xs text-muted hover:text-ink"><ArrowLeft className="h-3.5 w-3.5" />Pull requests</Link>
      <PageHeader title={`#${data.number} ${data.title || ""}`} subtitle={`${data.repository.full_name} · by ${data.author || "unknown"}`} />
      <Card title="Reviews" subtitle="One review per pushed commit" bodyClass="p-0">
        {!data.reviews.length ? <Empty title="Not reviewed yet">The review is queued or has not started.</Empty> : (
          <ul className="divide-y divide-line">
            {data.reviews.map((r) => (
              <li key={r.id}>
                <Link to={`/review/${r.id}`} className="flex flex-wrap items-center gap-4 px-5 py-3 hover:bg-raised/60">
                  <span className="font-mono text-xs text-muted">{shortSha(r.head_sha)}</span>
                  <StatusPill status={r.status} />
                  <RiskBadge label={r.risk_label} score={r.risk_score} />
                  <SeverityCounts counts={r.severity_counts} />
                  <span className="ml-auto text-xs text-muted">{formatDate(r.created_at)}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
