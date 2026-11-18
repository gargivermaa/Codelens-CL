"""
PR risk scoring - transparent, deterministic, documented.

    contribution(f) = SEVERITY_WEIGHT[f.severity]
                      * f.confidence
                      * (1.0 if f.status == "confirmed" else 0.5)
                      * (1.5 if f.category == "security" else 1.0)
                      * (1.25 if f.sensitive_file else 1.0)

    findings_points = sum(contribution(f))
    size_points     = min(2.0, log2(1 + changed_lines / 50))   # big diffs are riskier
    surface_points  = 1.0 if any changed file is security-sensitive else 0.0
    raw             = findings_points + size_points + surface_points

    risk_score = 1 - exp(-raw / SCALE)          # 0..1, saturating, SCALE = 10

Labels: >= 0.75 critical, >= 0.50 high, >= 0.25 medium, else low.
Floor: a confirmed critical finding makes the label at least "high"; a
confirmed high-severity security finding makes it at least "medium".

Examples: one confirmed high bug (confidence 0.9) in a 40-line change
-> raw 6*0.9 + 0.85 = 6.25 -> 0.46 "medium". The same as a security issue in an
auth file -> raw 6*0.9*1.5*1.25 + 0.85 + 1 = 11.97 -> 0.70 "high".
"""
import math

from app.review.schemas import ReviewFinding

SEVERITY_WEIGHT = {"critical": 10.0, "high": 6.0, "medium": 3.0, "low": 1.0, "info": 0.0}
SCALE = 10.0
LABELS = ((0.75, "critical"), (0.50, "high"), (0.25, "medium"), (0.0, "low"))
_ORDER = ["low", "medium", "high", "critical"]


def contribution(finding: ReviewFinding) -> float:
    return (SEVERITY_WEIGHT[finding.severity]
            * finding.confidence
            * (1.0 if finding.status == "confirmed" else 0.5)
            * (1.5 if finding.category == "security" else 1.0)
            * (1.25 if finding.sensitive_file else 1.0))


def score(findings: list[ReviewFinding], changed_lines: int, sensitive_change: bool) -> dict:
    per_finding = [round(contribution(f), 3) for f in findings]
    findings_points = sum(per_finding)
    size_points = min(2.0, math.log2(1 + max(0, changed_lines) / 50))
    surface_points = 1.0 if sensitive_change else 0.0
    raw = findings_points + size_points + surface_points
    value = 1 - math.exp(-raw / SCALE)

    label = next(name for threshold, name in LABELS if value >= threshold)
    floor = "low"
    if any(f.status == "confirmed" and f.severity == "critical" for f in findings):
        floor = "high"
    elif any(f.status == "confirmed" and f.category == "security" and f.severity == "high" for f in findings):
        floor = "medium"
    if _ORDER.index(floor) > _ORDER.index(label):
        label = floor

    return {
        "score": round(value, 4),
        "label": label,
        "raw": round(raw, 3),
        "findings_points": round(findings_points, 3),
        "size_points": round(size_points, 3),
        "surface_points": surface_points,
        "changed_lines": changed_lines,
        "per_finding": per_finding,
        "floor_applied": floor if floor != "low" else None,
        "formula": "1 - exp(-(findings + size + surface) / 10)",
    }
