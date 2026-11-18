import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, FileCode2, FileMinus, FilePlus, FileSymlink, MessageSquareWarning } from "lucide-react";
import { SEVERITY, SeverityBadge } from "./ui.jsx";

const HUNK = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/;

/** Parse a GitHub unified diff into display rows with old/new line numbers. */
export function parsePatch(patch) {
  const rows = [];
  let oldNo = 0, newNo = 0;
  for (const raw of (patch || "").split("\n")) {
    const m = raw.match(HUNK);
    if (m) {
      oldNo = Number(m[1]); newNo = Number(m[3]);
      rows.push({ type: "hunk", text: raw });
      continue;
    }
    const tag = raw[0];
    const text = raw.slice(1);
    if (tag === "+") rows.push({ type: "add", newNo: newNo++, text });
    else if (tag === "-") rows.push({ type: "del", oldNo: oldNo++, text });
    else if (tag === " " || raw === "") rows.push({ type: "ctx", oldNo: oldNo++, newNo: newNo++, text: raw === "" ? "" : text });
  }
  return rows;
}

const STATUS_ICON = { added: FilePlus, removed: FileMinus, renamed: FileSymlink };

function InlineFinding({ finding }) {
  const color = SEVERITY[finding.severity]?.color;
  return (
    <div className="mx-3 my-2 rounded-lg border border-line bg-raised p-3 font-sans" style={{ borderLeft: `3px solid ${color}` }}>
      <div className="flex flex-wrap items-center gap-2">
        <MessageSquareWarning className="h-4 w-4 text-muted" aria-hidden />
        <SeverityBadge severity={finding.severity} />
        <span className="text-xs capitalize text-muted">{finding.category}</span>
        <span className="text-sm font-medium text-ink">{finding.title}</span>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-muted">{finding.description}</p>
      {finding.suggested_fix && (
        <div className="mt-2">
          <div className="text-[11px] font-medium uppercase tracking-wide text-faint">Suggested fix</div>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded-md bg-canvas p-2 font-mono text-[12px] text-ink">{finding.suggested_fix}</pre>
        </div>
      )}
      <div className="mt-2 text-[11px] text-faint">
        {finding.posted_inline ? "Posted as an inline GitHub comment" : finding.status === "possible" ? "Possible concern (not posted inline)" : "Inline comment"} · confidence {finding.confidence.toFixed(2)} · {finding.source}
      </div>
    </div>
  );
}

function FileDiff({ file, findings, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen);
  const rows = useMemo(() => parsePatch(file.patch), [file.patch]);
  // A finding is shown under its comment line (or end line) on the new side.
  const anchor = (f) => f.comment_line || f.end_line;
  const byLine = useMemo(() => {
    const map = new Map();
    findings.forEach((f) => map.set(anchor(f), [...(map.get(anchor(f)) || []), f]));
    return map;
  }, [findings]);
  const flagged = useMemo(() => {
    const set = new Set();
    findings.forEach((f) => { for (let n = f.start_line; n <= f.end_line; n++) set.add(n); });
    return set;
  }, [findings]);
  const shownLines = new Set(rows.filter((r) => r.newNo).map((r) => r.newNo));
  const unanchored = findings.filter((f) => !shownLines.has(anchor(f)));
  const Icon = STATUS_ICON[file.status] || FileCode2;

  return (
    <div className="card overflow-hidden">
      <button onClick={() => setOpen(!open)} className="focus-ring flex w-full items-center gap-3 border-b border-line px-4 py-3 text-left hover:bg-raised/50">
        {open ? <ChevronDown className="h-4 w-4 text-muted" /> : <ChevronRight className="h-4 w-4 text-muted" />}
        <Icon className="h-4 w-4 text-muted" aria-hidden />
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-ink">
          {file.previous_path && file.previous_path !== file.path && <span className="text-faint">{file.previous_path} → </span>}
          {file.path}
        </span>
        {findings.length > 0 && <span className="rounded-md bg-serious/15 px-2 py-0.5 text-xs text-serious">{findings.length} finding{findings.length > 1 ? "s" : ""}</span>}
        <span className="text-xs tabular-nums text-good">+{file.additions}</span>
        <span className="text-xs tabular-nums text-critical">−{file.deletions}</span>
      </button>
      {open && (
        file.skip_reason ? (
          <div className="px-4 py-3 text-xs text-muted">Not reviewed: {file.skip_reason}</div>
        ) : !file.patch ? (
          <div className="px-4 py-3 text-xs text-muted">No textual diff available.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse font-mono text-[12.5px] leading-6">
              <tbody>
                {rows.map((row, i) => {
                  if (row.type === "hunk") {
                    return <tr key={i}><td colSpan={3} className="bg-accent2/10 px-4 text-[12px] text-accent2">{row.text}</td></tr>;
                  }
                  const isFlagged = row.newNo && flagged.has(row.newNo) && row.type !== "del";
                  const bg = row.type === "add" ? "bg-good/10" : row.type === "del" ? "bg-critical/10" : "";
                  const sign = row.type === "add" ? "+" : row.type === "del" ? "−" : " ";
                  const lineFindings = row.type !== "del" ? byLine.get(row.newNo) : null;
                  return [
                    <tr key={i} className={`${bg} ${isFlagged ? "outline outline-1 -outline-offset-1 outline-serious/60" : ""}`}>
                      <td className="w-12 select-none border-r border-line px-2 text-right text-faint">{row.oldNo ?? ""}</td>
                      <td className="w-12 select-none border-r border-line px-2 text-right text-faint">{row.newNo ?? ""}</td>
                      <td className="whitespace-pre px-3 text-ink"><span className="mr-2 select-none text-faint">{sign}</span>{row.text}</td>
                    </tr>,
                    lineFindings && (
                      <tr key={`${i}-f`}><td colSpan={3} className="bg-canvas/60">
                        {lineFindings.map((f) => <InlineFinding key={f.id} finding={f} />)}
                      </td></tr>
                    ),
                  ];
                })}
              </tbody>
            </table>
            {unanchored.length > 0 && (
              <div className="border-t border-line bg-canvas/60 py-1">
                <div className="px-4 pt-2 text-[11px] uppercase tracking-wide text-faint">Findings outside the shown diff</div>
                {unanchored.map((f) => <InlineFinding key={f.id} finding={f} />)}
              </div>
            )}
          </div>
        )
      )}
    </div>
  );
}

export default function DiffViewer({ files, findings }) {
  if (!files?.length) return <div className="card px-5 py-8 text-center text-sm text-muted">No changed files were stored for this review.</div>;
  return (
    <div className="space-y-4">
      {files.map((file) => {
        const fileFindings = findings.filter((f) => f.file === file.path);
        return <FileDiff key={file.path} file={file} findings={fileFindings} defaultOpen={!file.skip_reason} />;
      })}
    </div>
  );
}
