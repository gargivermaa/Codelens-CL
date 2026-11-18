import { useCallback, useEffect, useState } from "react";

export async function apiGet(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch { /* not JSON */ }
    const error = new Error(`${response.status} ${detail}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

/** Fetch JSON with loading / error state; `reload` refetches. */
export function useApi(path) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const load = useCallback(() => {
    if (!path) return;
    setState((s) => ({ ...s, loading: true, error: null }));
    apiGet(path)
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((error) => setState({ data: null, error, loading: false }));
  }, [path]);
  useEffect(load, [load]);
  return { ...state, reload: load };
}

export function timeAgo(iso) {
  if (!iso) return "—";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const units = [["d", 86400], ["h", 3600], ["m", 60]];
  for (const [unit, size] of units) if (seconds >= size) return `${Math.floor(seconds / size)}${unit} ago`;
  return "just now";
}

export function formatDate(iso) {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";
}

export const shortSha = (sha) => (sha ? sha.slice(0, 7) : "—");
