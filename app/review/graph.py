"""
LangGraph review pipeline.

    understand_changes -> analyze_changed_code -> retrieve_context -> detect_bugs
        -> [route] security_analysis (LLM + rules)   if security-sensitive
                   security_rules_only (rules)       otherwise
        -> quality_analysis -> validate_findings -> deduplicate_findings
        -> assign_severity -> score_risk -> generate_review

Each node returns a partial state update. LLM nodes never raise: a failure is
recorded in `errors` and the review continues (and says what is missing).
Findings are never invented to fill a gap.

The security routing implements the locked architecture decision: one
general review, plus an extra security-focused LLM pass only when the change
touches security-sensitive code (path/name/content signals or a rule hit).
"""
import logging
import operator
import re
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.chunker import chunk_source
from app.core.diff_parser import ChangedFile
from app.core.retriever import HybridRetriever, RetrievalResult, RetrievedChunk
from app.review import risk as risk_scoring
from app.review import security_rules
from app.review.llm import GeminiReviewLLM, LLMError
from app.review.prompts import SYSTEM_PROMPT, build_prompt
from app.review.schemas import SEVERITY_RANK, LLMFindingList, ReviewFinding

logger = logging.getLogger("codelens.review")

SENSITIVE = re.compile(
    r"(?i)auth|login|logout|passw|token|secret|session|cookie|crypt|cipher|hash|payment|billing|owner|user_id|"
    r"checkout|invoice|permission|role|admin|acl|oauth|jwt|csrf|sql|query|upload|deserial|exec|shell|"
    r"subprocess|sanitiz|escape|webhook|signature|credential|key"
)

MAX_UNITS_FOR_RETRIEVAL = 12
MAX_PATCH_LINES_IN_PROMPT = 400
MAX_UNIT_LINES_IN_PROMPT = 150
MAX_PROMPT_CHARS = 90_000
CONFIRM_CONFIDENCE = 0.7


@dataclass
class ChangedUnit:
    file: str
    qualified_name: str
    type: str
    start_line: int
    end_line: int
    text: str
    added_lines: list[int] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.file}::{self.qualified_name}:{self.start_line}"


class ReviewState(TypedDict, total=False):
    repo_full_name: str
    pr_number: int
    pr_title: str
    pr_body: str
    files: list[ChangedFile]
    file_contents: dict[str, str]
    # understand_changes
    reviewable_files: list[ChangedFile]
    sensitive_files: list[str]
    change_stats: dict
    # analyze / retrieve
    units: list[ChangedUnit]
    contexts: dict[str, list[RetrievedChunk]]
    context_labels: dict[str, str]          # point id -> "C1"
    retrievals: list[RetrievalResult]
    # findings
    raw_findings: Annotated[list[ReviewFinding], operator.add]
    findings: list[ReviewFinding]
    dropped: Annotated[list[dict], operator.add]
    risk: dict
    summary_markdown: str
    inline_findings: list[ReviewFinding]
    # bookkeeping
    errors: Annotated[list[str], operator.add]
    llm_calls: Annotated[list[dict], operator.add]
    timings: Annotated[list[tuple[str, int]], operator.add]
    security_llm_used: bool


def _timed(name, fn):
    def wrapper(state):
        started = time.perf_counter()
        update = fn(state) or {}
        update.setdefault("timings", [])
        update["timings"] = update["timings"] + [(name, int((time.perf_counter() - started) * 1000))]
        return update
    wrapper.__name__ = name
    return wrapper


def _number(text: str, start_line: int) -> str:
    return "\n".join(f"{start_line + i:>5}   {line}" for i, line in enumerate(text.split("\n")))


class ReviewPipeline:
    def __init__(self, llm: GeminiReviewLLM | None, retriever: HybridRetriever | None,
                 top_k: int = 5):
        self.llm = llm
        self.retriever = retriever
        self.top_k = top_k
        self.graph = self._build()

    # ------------------------------------------------------------------ graph
    def _build(self):
        g = StateGraph(ReviewState)
        nodes = {
            "understand_changes": self.understand_changes,
            "analyze_changed_code": self.analyze_changed_code,
            "retrieve_context": self.retrieve_context,
            "detect_bugs": self.detect_bugs,
            "security_analysis": self.security_analysis,
            "security_rules_only": self.security_rules_only,
            "quality_analysis": self.quality_analysis,
            "validate_findings": self.validate_findings,
            "deduplicate_findings": self.deduplicate_findings,
            "assign_severity": self.assign_severity,
            "score_risk": self.score_risk,
            "generate_review": self.generate_review,
        }
        for name, fn in nodes.items():
            g.add_node(name, _timed(name, fn))
        g.add_edge(START, "understand_changes")
        g.add_edge("understand_changes", "analyze_changed_code")
        g.add_edge("analyze_changed_code", "retrieve_context")
        g.add_edge("retrieve_context", "detect_bugs")
        g.add_conditional_edges("detect_bugs", self.route_security,
                                {"llm": "security_analysis", "rules": "security_rules_only"})
        g.add_edge("security_analysis", "quality_analysis")
        g.add_edge("security_rules_only", "quality_analysis")
        g.add_edge("quality_analysis", "validate_findings")
        g.add_edge("validate_findings", "deduplicate_findings")
        g.add_edge("deduplicate_findings", "assign_severity")
        g.add_edge("assign_severity", "score_risk")
        g.add_edge("score_risk", "generate_review")
        g.add_edge("generate_review", END)
        return g.compile()

    def run(self, state: ReviewState) -> ReviewState:
        initial: ReviewState = {"raw_findings": [], "dropped": [], "errors": [], "llm_calls": [],
                                "timings": [], **state}
        return self.graph.invoke(initial)

    # ------------------------------------------------------------------ nodes
    def understand_changes(self, state: ReviewState) -> dict:
        files = state["files"]
        reviewable = [f for f in files if f.reviewable]
        contents = state.get("file_contents", {})
        sensitive = []
        for f in reviewable:
            # Removed lines count too: deleting an authorization or validation check
            # (e.g. "user_id != user['id'] and role != 'admin'") is a security change
            # even when the added lines look harmless.
            changed_text = "\n".join([*f.added_lines.values(), *f.removed_lines.values()])
            if SENSITIVE.search(f.path) or SENSITIVE.search(changed_text[:20_000]):
                sensitive.append(f.path)
        stats = {
            "files_total": len(files),
            "files_reviewable": len(reviewable),
            "files_skipped": {f.path: f.skip_reason for f in files if not f.reviewable},
            "additions": sum(f.additions or len(f.added_lines) for f in files),
            "deletions": sum(f.deletions or len(f.removed_lines) for f in files),
            "truncated_files": [f.path for f in reviewable if f.patch_truncated],
            "missing_content": [f.path for f in reviewable if f.path not in contents],
        }
        return {"reviewable_files": reviewable, "sensitive_files": sensitive, "change_stats": stats}

    def analyze_changed_code(self, state: ReviewState) -> dict:
        """Map added lines to the semantic units (functions/classes) that contain them."""
        units: list[ChangedUnit] = []
        contents = state.get("file_contents", {})
        for f in state["reviewable_files"]:
            added = set(f.added_lines)
            if not added:
                continue
            content = contents.get(f.path)
            covered: set[int] = set()
            if content is not None:
                chunks = chunk_source(content.encode("utf-8"), f.path)
                # Innermost units first so a method wins over its class.
                for chunk in sorted(chunks, key=lambda c: c.end_line - c.start_line):
                    lines = {n for n in added if chunk.start_line <= n <= chunk.end_line} - covered
                    if not lines:
                        continue
                    if chunk.type == "class" and not chunk.is_split and any(
                            c.type == "method" and c.qualified_name.startswith(chunk.qualified_name + ".")
                            and c.start_line <= min(lines) <= c.end_line for c in chunks):
                        continue
                    units.append(ChangedUnit(f.path, chunk.qualified_name, chunk.type, chunk.start_line,
                                             chunk.end_line, chunk.text, sorted(lines)))
                    covered |= lines
            # Module-level additions (imports, constants, script code).
            rest = sorted(added - covered)
            if rest:
                block_start = block_end = rest[0]
                for n in rest[1:] + [None]:
                    if n is not None and n <= block_end + 3:
                        block_end = n
                        continue
                    text = "\n".join(f.added_lines.get(i, "") for i in range(block_start, block_end + 1))
                    units.append(ChangedUnit(f.path, "<module>", "module", block_start, block_end, text,
                                             [i for i in rest if block_start <= i <= block_end]))
                    if n is not None:
                        block_start = block_end = n
        return {"units": units}

    def retrieve_context(self, state: ReviewState) -> dict:
        if self.retriever is None:
            return {"contexts": {}, "context_labels": {}, "retrievals": [],
                    "errors": ["Repository context unavailable: repository is not indexed."]}
        contexts: dict[str, list[RetrievedChunk]] = {}
        retrievals: list[RetrievalResult] = []
        errors: list[str] = []
        units = sorted(state["units"], key=lambda u: -len(u.added_lines))[:MAX_UNITS_FOR_RETRIEVAL]
        for unit in units:
            query = f"{unit.file} {unit.qualified_name}\n{unit.text[:1500]}"

            def exclude(payload, unit=unit):
                # The changed unit's own previous version is already in the diff.
                return payload.get("file") == unit.file and payload.get("qualified_name") == unit.qualified_name

            try:
                result = self.retriever.search(query, top_k=self.top_k, exclude=exclude)
            except Exception as exc:  # retrieval problems must not kill the review
                errors.append(f"Context retrieval failed for {unit.file}:{unit.qualified_name}: {exc}")
                continue
            contexts[unit.key] = result.chunks
            retrievals.append(result)
            if result.degraded_reason and not any("degraded" in e for e in errors):
                errors.append(f"Retrieval degraded: {result.degraded_reason}")
        labels: dict[str, str] = {}
        for chunks in contexts.values():
            for chunk in chunks:
                labels.setdefault(chunk.point_id, f"C{len(labels) + 1}")
        if len(state["units"]) > MAX_UNITS_FOR_RETRIEVAL:
            errors.append(f"Context retrieved for the {MAX_UNITS_FOR_RETRIEVAL} largest changed units "
                          f"of {len(state['units'])}.")
        return {"contexts": contexts, "context_labels": labels, "retrievals": retrievals, "errors": errors}

    def _prompt_blocks(self, state: ReviewState) -> tuple[str, str]:
        contents = state.get("file_contents", {})
        units_by_file: dict[str, list[ChangedUnit]] = {}
        for unit in state["units"]:
            units_by_file.setdefault(unit.file, []).append(unit)
        parts = []
        for f in state["reviewable_files"]:
            if not f.added_lines:
                continue
            block = [f'<file path="{f.path}" status="{f.status}">',
                     f.annotated_patch(MAX_PATCH_LINES_IN_PROMPT)]
            if f.path in contents:
                for unit in units_by_file.get(f.path, []):
                    if unit.type == "module":
                        continue
                    lines = unit.text.split("\n")[:MAX_UNIT_LINES_IN_PROMPT]
                    block.append(f"Full current code of {unit.type} {unit.qualified_name} "
                                 f"(lines {unit.start_line}-{unit.end_line}):")
                    block.append(_number("\n".join(lines), unit.start_line))
            block.append("</file>")
            parts.append("\n".join(block))
        files_block = "\n\n".join(parts)

        seen: set[str] = set()
        context_parts = []
        labels = state.get("context_labels", {})
        for chunks in state.get("contexts", {}).values():
            for chunk in chunks:
                if chunk.point_id in seen:
                    continue
                seen.add(chunk.point_id)
                p = chunk.payload
                context_parts.append(f"[{labels[chunk.point_id]}] {p.get('type')} {p.get('qualified_name')} "
                                     f"({chunk.location})\n{p.get('text', '')}")
        context_block = "\n\n".join(context_parts)
        budget = MAX_PROMPT_CHARS - len(files_block)
        if len(context_block) > budget:
            context_block = context_block[:max(0, budget)] + "\n... (context truncated)"
        return files_block[:MAX_PROMPT_CHARS], context_block

    def _llm_pass(self, state: ReviewState, focus: str) -> dict:
        if not any(f.added_lines for f in state["reviewable_files"]):
            return {}
        if self.llm is None:
            return {"errors": [f"{focus} analysis skipped: no LLM configured (GEMINI_API_KEY missing)."]}
        files_block, context_block = self._prompt_blocks(state)
        prompt = build_prompt(focus, state.get("pr_title", ""), state.get("pr_body", "") or "",
                              files_block, context_block)
        try:
            result = self.llm.generate(SYSTEM_PROMPT, prompt, LLMFindingList)
        except LLMError as exc:
            logger.warning("LLM %s pass failed: %s", focus, exc)
            return {"errors": [f"{focus} analysis failed: {exc}"],
                    "llm_calls": [{"focus": focus, "ok": False, "error": str(exc)[:300]}]}
        sensitive = set(state.get("sensitive_files", []))
        raw = [ReviewFinding(**item.model_dump(exclude={"file"}), file=item.file, status="possible",
                             source=f"llm:{focus}", sensitive_file=item.file in sensitive)
               for item in result.data.findings]
        call = {"focus": focus, "ok": True, "model": result.model, "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens, "latency_ms": result.latency_ms,
                "attempts": result.attempts, "findings": len(raw),
                "raw_output": list(getattr(self.llm, "last_raw_outputs", []))}
        return {"raw_findings": raw, "llm_calls": [call]}

    def detect_bugs(self, state: ReviewState) -> dict:
        return self._llm_pass(state, "bug")

    def route_security(self, state: ReviewState) -> str:
        if state.get("sensitive_files"):
            return "llm"
        for f in state["reviewable_files"]:
            if security_rules.scan_file(f):
                return "llm"
        return "rules"

    def _rule_findings(self, state: ReviewState) -> list[ReviewFinding]:
        sensitive = set(state.get("sensitive_files", []))
        out = []
        for f in state["reviewable_files"]:
            out.extend(security_rules.scan_file(f, sensitive=f.path in sensitive))
        return out

    def security_analysis(self, state: ReviewState) -> dict:
        update = self._llm_pass(state, "security")
        update["raw_findings"] = update.get("raw_findings", []) + self._rule_findings(state)
        update["security_llm_used"] = True
        return update

    def security_rules_only(self, state: ReviewState) -> dict:
        return {"raw_findings": self._rule_findings(state), "security_llm_used": False}

    def quality_analysis(self, state: ReviewState) -> dict:
        return self._llm_pass(state, "quality")

    def validate_findings(self, state: ReviewState) -> dict:
        """Ground every finding in the actual diff; drop what cannot be verified."""
        files = {f.path: f for f in state["reviewable_files"]}
        contents = state.get("file_contents", {})
        labels = set(state.get("context_labels", {}).values())
        valid: list[ReviewFinding] = []
        dropped: list[dict] = []

        def norm(text: str) -> str:
            return re.sub(r"\s+", " ", text).strip()

        for finding in state.get("raw_findings", []):
            if finding.source.startswith("rule:"):
                valid.append(finding)
                continue
            changed = files.get(finding.file)
            if changed is None:
                dropped.append({"title": finding.title, "reason": f"file {finding.file!r} is not in the reviewed diff"})
                continue
            start, end = sorted((finding.start_line, finding.end_line))
            evidence = norm(finding.evidence)
            if len(evidence) < 3:
                dropped.append({"title": finding.title, "reason": "no evidence quoted"})
                continue
            added_text = norm("\n".join(changed.added_lines.values()))
            content = contents.get(finding.file)
            in_added = evidence in added_text
            in_file = in_added or (content is not None and evidence in norm(content))
            if not in_file:
                dropped.append({"title": finding.title, "reason": "evidence not found in the file (unverifiable)"})
                continue
            # Locate the evidence on the added lines to correct line numbers if needed.
            evidence_lines = [n for n, text in changed.added_lines.items()
                              if norm(text) and (norm(text) in evidence or evidence in norm(text))]
            overlap = [n for n in changed.added_lines if start <= n <= end]
            if not overlap and evidence_lines:
                start, end = min(evidence_lines), max(evidence_lines)
                overlap = evidence_lines
            in_changed = bool(overlap)
            confirmed = in_added and in_changed and finding.confidence >= CONFIRM_CONFIDENCE
            comment_line = None
            if confirmed:
                comment_line = max(n for n in overlap if n in changed.commentable_lines) if any(
                    n in changed.commentable_lines for n in overlap) else None
            valid.append(finding.model_copy(update={
                "start_line": start, "end_line": end, "status": "confirmed" if confirmed else "possible",
                "comment_line": comment_line, "evidence_verified": True, "in_changed_lines": in_changed,
                "context_refs": [r.strip("[]") for r in finding.context_refs if r.strip("[]") in labels],
            }))
        return {"findings": valid, "dropped": dropped}

    def deduplicate_findings(self, state: ReviewState) -> dict:
        def words(text):
            return set(re.findall(r"[a-z0-9]+", text.lower()))

        ordered = sorted(state.get("findings", []), key=lambda f: (
            -(f.status == "confirmed"), -f.severity_rank, -f.confidence, f.file, f.start_line))
        kept: list[ReviewFinding] = []
        dropped = []
        for finding in ordered:
            duplicate_of = None
            for other in kept:
                if other.file != finding.file:
                    continue
                near = finding.start_line <= other.end_line + 3 and other.start_line <= finding.end_line + 3
                if not near:
                    continue
                a, b = words(finding.title), words(other.title)
                similar = bool(a and b) and len(a & b) / len(a | b) >= 0.5
                same_evidence = finding.evidence and finding.evidence.strip() == other.evidence.strip()
                if finding.category == other.category or similar or same_evidence:
                    duplicate_of = other
                    break
            if duplicate_of is None:
                kept.append(finding)
            else:
                if finding.source not in duplicate_of.source:
                    index = kept.index(duplicate_of)
                    kept[index] = duplicate_of.model_copy(update={"source": f"{duplicate_of.source}+{finding.source}"})
                dropped.append({"title": finding.title, "reason": f"duplicate of '{duplicate_of.title}'"})
        return {"findings": kept, "dropped": dropped}

    def assign_severity(self, state: ReviewState) -> dict:
        """Normalize severities with documented caps (see ARCHITECTURE.md)."""
        out = []
        for f in state.get("findings", []):
            severity = f.severity
            if f.status == "possible" and SEVERITY_RANK[severity] > SEVERITY_RANK["medium"]:
                severity = "medium"            # unverified concerns are never high/critical
            if f.confidence < 0.5 and SEVERITY_RANK[severity] > SEVERITY_RANK["low"]:
                severity = "low"
            if f.category == "security" and f.status == "confirmed" and severity == "info":
                severity = "low"               # a confirmed security issue is never "info"
            out.append(f.model_copy(update={"severity": severity}))
        out.sort(key=lambda f: (-(f.status == "confirmed"), -SEVERITY_RANK[f.severity], -f.confidence))
        return {"findings": out}

    def score_risk(self, state: ReviewState) -> dict:
        stats = state["change_stats"]
        return {"risk": risk_scoring.score(state.get("findings", []),
                                           stats["additions"] + stats["deletions"],
                                           bool(state.get("sensitive_files")))}

    def generate_review(self, state: ReviewState) -> dict:
        findings = state.get("findings", [])
        inline = [f for f in findings if f.status == "confirmed" and f.comment_line]
        return {"summary_markdown": render_summary(state, findings), "inline_findings": inline}


# ---------------------------------------------------------------- rendering
_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}


def render_inline_body(f: ReviewFinding, labels_to_location: dict[str, str] | None = None) -> str:
    body = [f"**{_ICON[f.severity]} [{f.severity.upper()} · {f.category}] {f.title}**", "", f.description]
    if f.explanation:
        body += ["", f"**Why it matters:** {f.explanation}"]
    if f.suggested_fix:
        fix = f.suggested_fix.strip()
        body += ["", "**Suggested fix:**", fix if "\n" not in fix and len(fix) < 160 else f"```python\n{fix}\n```"]
    if f.context_refs and labels_to_location:
        refs = ", ".join(f"`{labels_to_location[r]}`" for r in f.context_refs if r in labels_to_location)
        if refs:
            body += ["", f"Related repository code: {refs}"]
    body += ["", f"<sub>CodeLens CI · confidence {f.confidence:.2f} · {f.source}</sub>"]
    return "\n".join(body)


def context_locations(state: ReviewState) -> dict[str, str]:
    labels = state.get("context_labels", {})
    locations = {}
    for chunks in state.get("contexts", {}).values():
        for chunk in chunks:
            locations[labels[chunk.point_id]] = chunk.location
    return locations


def render_summary(state: ReviewState, findings: list[ReviewFinding]) -> str:
    risk = state["risk"]
    stats = state["change_stats"]
    confirmed = [f for f in findings if f.status == "confirmed"]
    possible = [f for f in findings if f.status == "possible"]
    lines = [
        "## 🔍 CodeLens CI review",
        "",
        f"**Risk: {risk['label'].upper()} ({risk['score']:.2f})** · {len(confirmed)} confirmed issue(s), "
        f"{len(possible)} possible concern(s) · {stats['files_reviewable']} file(s) reviewed, "
        f"+{stats['additions']}/-{stats['deletions']} lines",
    ]
    contexts = state.get("contexts", {})
    if contexts:
        n_ctx = len(state.get("context_labels", {}))
        modes = {r.mode for r in state.get("retrievals", [])}
        lines.append(f"Repository context: {n_ctx} related code chunk(s) retrieved ({'/'.join(sorted(modes))}).")
    if confirmed:
        lines += ["", "### Issues", "", "| | Severity | Category | Location | Issue |", "|---|---|---|---|---|"]
        for f in confirmed:
            lines.append(f"| {_ICON[f.severity]} | {f.severity} | {f.category} | "
                         f"`{f.file}:{f.start_line}` | {f.title.replace('|', '/')} |")
    if possible:
        lines += ["", "<details><summary>Possible concerns (not fully verified)</summary>", ""]
        for f in possible:
            lines.append(f"- **{f.title}** (`{f.file}:{f.start_line}`, {f.category}, "
                         f"confidence {f.confidence:.2f}): {f.description}")
        lines += ["", "</details>"]
    if not findings:
        lines += ["", "No issues found in the reviewed changes."]
    notes = list(dict.fromkeys(state.get("errors", [])))
    skipped = stats.get("files_skipped", {})
    if stats.get("truncated_files"):
        notes.append("Large diffs were reviewed partially: " + ", ".join(stats["truncated_files"]))
    if notes or skipped:
        lines += ["", "<details><summary>Review notes</summary>", ""]
        lines += [f"- {n}" for n in notes]
        if skipped:
            lines.append(f"- Not reviewed: " + ", ".join(f"`{p}` ({r})" for p, r in list(skipped.items())[:20]))
        lines += ["", "</details>"]
    lines += ["", f"<sub>Risk = {risk['formula']} · findings {risk['findings_points']} + size "
                  f"{risk['size_points']} + surface {risk['surface_points']}</sub>"]
    return "\n".join(lines)


def to_json_safe(state: ReviewState) -> dict[str, Any]:
    """Structured pipeline output for reviews.raw_output (no secrets, no full context text)."""
    return {
        "change_stats": state.get("change_stats"),
        "sensitive_files": state.get("sensitive_files"),
        "units": [{"file": u.file, "name": u.qualified_name, "lines": [u.start_line, u.end_line],
                   "added": len(u.added_lines)} for u in state.get("units", [])],
        "context": {label: loc for label, loc in context_locations(state).items()},
        "findings": [f.model_dump() for f in state.get("findings", [])],
        "dropped": state.get("dropped", []),
        "risk": state.get("risk"),
        "llm_calls": state.get("llm_calls", []),
        "security_llm_used": state.get("security_llm_used"),
        "errors": state.get("errors", []),
        "timings_ms": dict(state.get("timings", [])),
    }
