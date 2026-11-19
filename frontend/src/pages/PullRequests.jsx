import { useState } from "react";
import { useApi } from "../api.js";
import { Card, Empty, ErrorState, Loading, PageHeader, RISK } from "../components/ui.jsx";
import PullTable from "../components/PullTable.jsx";

const STATUSES = ["completed", "processing", "failed"];

function Select({ label, value, onChange, options }) {
  return (
    <label className="flex items-center gap-2 text-xs text-muted">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="focus-ring rounded-md border border-line bg-surface px-2 py-1.5 text-xs text-ink">
        <option value="">All</option>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}

export default function PullRequests() {
  const [risk, setRisk] = useState("");
  const [status, setStatus] = useState("");
  const [repo, setRepo] = useState("");
  const repos = useApi("/api/repositories");
  const params = new URLSearchParams({ ...(risk && { risk }), ...(status && { status }), ...(repo && { repo }) });
  const { data, error, loading, reload } = useApi(`/api/pulls?${params}`);
  return (
    <>
      <PageHeader title="Pull requests" subtitle="Every pull request CodeLens has seen, with its latest review." />
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <Select label="Repository" value={repo} onChange={setRepo}
          options={(repos.data?.repositories || []).map((r) => [String(r.github_id), r.full_name])} />
        <Select label="Risk" value={risk} onChange={setRisk} options={Object.entries(RISK).map(([k, v]) => [k, v.label])} />
        <Select label="Status" value={status} onChange={setStatus} options={STATUSES.map((s) => [s, s])} />
      </div>
      {loading && !data ? <Loading /> : error ? <ErrorState error={error} onRetry={reload} /> : (
        <Card bodyClass="p-0">
          {data.pull_requests.length ? <PullTable pulls={data.pull_requests} />
            : <Empty title="No pull requests match">Change the filters, or open a pull request in a connected repository.</Empty>}
        </Card>
      )}
    </>
  );
}
