import { Link, useNavigate } from "react-router-dom";
import { ExternalLink } from "lucide-react";
import { RiskBadge, SeverityCounts, StatusPill } from "./ui.jsx";
import { timeAgo } from "../api.js";

/** Pull requests with their latest review. Rows open the review (or the PR history if not reviewed). */
export default function PullTable({ pulls, showRepo = true }) {
  const navigate = useNavigate();
  const target = (pr) => (pr.latest_review ? `/review/${pr.latest_review.id}` : `/pulls/${pr.id}`);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead>
          <tr className="border-b border-line">
            <th className="table-head px-5 py-3">Pull request</th>
            {showRepo && <th className="table-head px-3 py-3">Repository</th>}
            <th className="table-head px-3 py-3">Author</th>
            <th className="table-head px-3 py-3">Status</th>
            <th className="table-head px-3 py-3">Risk</th>
            <th className="table-head px-3 py-3">Findings</th>
            <th className="table-head px-5 py-3 text-right">Reviewed</th>
          </tr>
        </thead>
        <tbody>
          {pulls.map((pr) => {
            const review = pr.latest_review;
            return (
              <tr key={pr.id} onClick={() => navigate(target(pr))}
                className="cursor-pointer border-b border-line/60 last:border-0 hover:bg-raised/60">
                <td className="max-w-[340px] px-5 py-3">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-muted">#{pr.number}</span>
                    <Link to={target(pr)} onClick={(e) => e.stopPropagation()} className="focus-ring truncate font-medium text-ink hover:text-accent">
                      {pr.title || "(untitled)"}
                    </Link>
                    <a href={pr.html_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
                      className="text-faint hover:text-ink" aria-label="Open on GitHub"><ExternalLink className="h-3.5 w-3.5" /></a>
                  </div>
                </td>
                {showRepo && <td className="px-3 py-3 font-mono text-xs text-muted">{pr.repository.full_name}</td>}
                <td className="px-3 py-3 text-muted">{pr.author || "—"}</td>
                <td className="px-3 py-3"><StatusPill status={review?.status || pr.status} /></td>
                <td className="px-3 py-3"><RiskBadge label={review?.risk_label} score={review?.risk_score} /></td>
                <td className="px-3 py-3"><SeverityCounts counts={review?.severity_counts} /></td>
                <td className="whitespace-nowrap px-5 py-3 text-right text-xs text-muted">{timeAgo(review?.created_at || pr.created_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
