import { useNavigate } from "react-router-dom";
import { CheckCircle2, CircleDashed, Lock, Globe } from "lucide-react";
import { formatDate, shortSha, timeAgo, useApi } from "../api.js";
import { Card, Empty, ErrorState, Loading, PageHeader } from "../components/ui.jsx";

export default function Repositories() {
  const { data, error, loading, reload } = useApi("/api/repositories");
  const navigate = useNavigate();
  return (
    <>
      <PageHeader title="Repositories" subtitle="Repositories the GitHub App can access, and the state of their code index." />
      {loading && !data ? <Loading /> : error ? <ErrorState error={error} onRetry={reload} /> : (
        <Card bodyClass="p-0">
          {!data.repositories.length ? (
            <Empty title="No repositories connected">Install the CodeLens GitHub App on a repository. It appears here immediately, and is indexed on its first pull request.</Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-sm">
                <thead><tr className="border-b border-line">
                  <th className="table-head px-5 py-3">Repository</th>
                  <th className="table-head px-3 py-3">Default branch</th>
                  <th className="table-head px-3 py-3">Index</th>
                  <th className="table-head px-3 py-3">Last indexed</th>
                  <th className="table-head px-3 py-3 text-right">PRs</th>
                  <th className="table-head px-3 py-3 text-right">Reviews</th>
                  <th className="table-head px-5 py-3 text-right">Last review</th>
                </tr></thead>
                <tbody>
                  {data.repositories.map((repo) => (
                    <tr key={repo.github_id} onClick={() => navigate(`/repositories/${repo.github_id}`)}
                      className="cursor-pointer border-b border-line/60 last:border-0 hover:bg-raised/60">
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-2 font-medium text-ink">
                          {repo.private ? <Lock className="h-3.5 w-3.5 text-muted" aria-label="Private" /> : <Globe className="h-3.5 w-3.5 text-muted" aria-label="Public" />}
                          {repo.full_name}
                        </div>
                      </td>
                      <td className="px-3 py-3 font-mono text-xs text-muted">{repo.default_branch || "—"}</td>
                      <td className="px-3 py-3">
                        {repo.indexed ? (
                          <span className="inline-flex items-center gap-1.5 text-xs text-good"><CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
                            {repo.indexed_chunks} chunks @ <span className="font-mono">{shortSha(repo.indexed_sha)}</span></span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 text-xs text-muted"><CircleDashed className="h-3.5 w-3.5" aria-hidden />Not indexed yet</span>
                        )}
                      </td>
                      <td className="px-3 py-3 text-xs text-muted">{formatDate(repo.indexed_at)}</td>
                      <td className="px-3 py-3 text-right tabular-nums">{repo.pull_requests}</td>
                      <td className="px-3 py-3 text-right tabular-nums">{repo.reviews}</td>
                      <td className="px-5 py-3 text-right text-xs text-muted">{timeAgo(repo.last_review_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </>
  );
}
