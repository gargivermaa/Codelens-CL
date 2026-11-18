import { AlertTriangle, CheckCircle2, Circle, Inbox, Loader2, OctagonAlert, RefreshCw, ShieldAlert } from "lucide-react";

// Fixed status palette (never reused for categories). Every use also shows a text label.
export const RISK = {
  low: { label: "Low", color: "#0ca30c", Icon: CheckCircle2 },
  medium: { label: "Medium", color: "#fab219", Icon: AlertTriangle },
  high: { label: "High", color: "#ec835a", Icon: ShieldAlert },
  critical: { label: "Critical", color: "#d03b3b", Icon: OctagonAlert },
};

export const SEVERITY = {
  critical: { label: "Critical", color: "#d03b3b" },
  high: { label: "High", color: "#ec835a" },
  medium: { label: "Medium", color: "#fab219" },
  low: { label: "Low", color: "#3987e5" },
  info: { label: "Info", color: "#8f98ad" },
};
export const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"];

export function PageHeader({ title, subtitle, actions }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, subtitle, action, children, className = "", bodyClass = "p-5" }) {
  return (
    <section className={`card ${className}`}>
      {(title || action) && (
        <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-ink">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      <div className={bodyClass}>{children}</div>
    </section>
  );
}

export function StatCard({ label, value, hint, Icon, tone = "text-accent" }) {
  return (
    <div className="card p-5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-muted">{label}</span>
        {Icon && <Icon className={`h-4 w-4 ${tone}`} aria-hidden />}
      </div>
      <div className="mt-3 text-3xl font-semibold tabular-nums text-ink">{value ?? "—"}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function RiskBadge({ label, score, size = "sm" }) {
  if (!label) return <span className="text-xs text-faint">Not scored</span>;
  const risk = RISK[label] || RISK.low;
  const { Icon } = risk;
  const pad = size === "lg" ? "px-3 py-1.5 text-sm" : "px-2 py-0.5 text-xs";
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md border font-medium ${pad}`}
      style={{ color: risk.color, borderColor: `${risk.color}55`, background: `${risk.color}14` }}>
      <Icon className={size === "lg" ? "h-4 w-4" : "h-3.5 w-3.5"} aria-hidden />
      {risk.label}
      {score != null && <span className="tabular-nums text-ink/80">{score.toFixed(2)}</span>}
    </span>
  );
}

export function SeverityBadge({ severity }) {
  const s = SEVERITY[severity] || SEVERITY.info;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md bg-raised px-2 py-0.5 text-xs font-medium text-ink">
      <span className="h-2 w-2 rounded-full" style={{ background: s.color }} aria-hidden />
      {s.label}
    </span>
  );
}

/** Compact severity counts, e.g. "2 high · 1 medium" with colored dots. */
export function SeverityCounts({ counts }) {
  const items = SEVERITY_ORDER.filter((s) => counts?.[s]);
  if (!items.length) return <span className="text-xs text-faint">No findings</span>;
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {items.map((s) => (
        <span key={s} className="inline-flex items-center gap-1 text-xs text-muted">
          <span className="h-2 w-2 rounded-full" style={{ background: SEVERITY[s].color }} aria-hidden />
          <span className="tabular-nums text-ink">{counts[s]}</span> {SEVERITY[s].label.toLowerCase()}
        </span>
      ))}
    </span>
  );
}

const STATUS_STYLE = {
  completed: "text-good", analyzed: "text-accent2", running: "text-warning", pending: "text-muted",
  processing: "text-warning", failed: "text-critical",
};

export function StatusPill({ status }) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium capitalize ${STATUS_STYLE[status] || "text-muted"}`}>
      {status === "running" || status === "processing"
        ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
        : <Circle className="h-2 w-2 fill-current" aria-hidden />}
      {status || "unknown"}
    </span>
  );
}

export function Loading({ label = "Loading…" }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted" role="status">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> {label}
    </div>
  );
}

export function ErrorState({ error, onRetry }) {
  return (
    <div className="card flex flex-col items-center gap-3 px-6 py-12 text-center" role="alert">
      <OctagonAlert className="h-6 w-6 text-critical" aria-hidden />
      <div className="text-sm font-medium text-ink">Could not load data</div>
      <div className="max-w-md text-xs text-muted">{String(error?.message || error)} — is the API running on port 8000?</div>
      {onRetry && (
        <button onClick={onRetry} className="focus-ring mt-1 inline-flex items-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-xs text-ink hover:bg-raised">
          <RefreshCw className="h-3.5 w-3.5" aria-hidden /> Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ title, children, Icon = Inbox }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
      <Icon className="h-6 w-6 text-faint" aria-hidden />
      <div className="text-sm font-medium text-ink">{title}</div>
      {children && <div className="max-w-md text-xs leading-relaxed text-muted">{children}</div>}
    </div>
  );
}

export function Code({ children }) {
  return <code className="rounded bg-raised px-1.5 py-0.5 font-mono text-[12px] text-ink">{children}</code>;
}
