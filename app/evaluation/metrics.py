"""Retrieval and review-quality metrics (pure functions, unit-tested)."""
from statistics import mean, median


def precision_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    top = ranked[:k]
    return sum(1 for item in top if item in relevant) / k if k else 0.0


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    for index, item in enumerate(ranked, start=1):
        if item in relevant:
            return 1.0 / index
    return 0.0


def summarize_latencies(values_ms: list[float]) -> dict:
    if not values_ms:
        return {"n": 0}
    ordered = sorted(values_ms)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    return {"n": len(values_ms), "mean_ms": round(mean(values_ms), 2), "median_ms": round(median(values_ms), 2),
            "p95_ms": round(p95, 2), "max_ms": round(max(values_ms), 2)}


SECURITY = {"security"}
CORRECTNESS = {"bug", "performance"}
QUALITY = {"quality", "maintainability"}


def categories_compatible(predicted: str, expected: str) -> bool:
    for group in (SECURITY, CORRECTNESS, QUALITY):
        if expected in group:
            return predicted in group
    return predicted == expected


def match_findings(predicted: list[dict], expected: list[dict], tolerance: int = 2) -> tuple[int, int]:
    """Greedy one-to-one matching. A prediction matches an expected defect when it
    is in the same file, its line range is within `tolerance` lines of the
    defect line, and the categories are compatible. Returns (true_pos_predictions,
    matched_expected)."""
    used: set[int] = set()
    true_positive = 0
    for p in predicted:
        for index, e in enumerate(expected):
            if index in used or p["file"] != e["file"]:
                continue
            if p["start_line"] - tolerance <= e["line"] <= p["end_line"] + tolerance and \
                    categories_compatible(p["category"], e["category"]):
                used.add(index)
                true_positive += 1
                break
    return true_positive, len(used)


def review_scores(cases: list[dict]) -> dict:
    """cases: [{"predicted": [...], "expected": [...]}] -> precision/recall/FP rate."""
    tp = predicted_total = matched = expected_total = 0
    clean = clean_with_findings = 0
    for case in cases:
        t, m = match_findings(case["predicted"], case["expected"])
        tp += t
        matched += m
        predicted_total += len(case["predicted"])
        expected_total += len(case["expected"])
        if not case["expected"]:
            clean += 1
            clean_with_findings += 1 if case["predicted"] else 0
    return {
        "finding_precision": round(tp / predicted_total, 4) if predicted_total else None,
        "finding_recall": round(matched / expected_total, 4) if expected_total else None,
        "false_positive_findings": predicted_total - tp,
        "clean_case_false_positive_rate": round(clean_with_findings / clean, 4) if clean else None,
        "predicted": predicted_total, "expected": expected_total, "true_positives": tp,
    }


# ---------------------------------------------------------------------------
# Review evaluation v2 (strict / lenient matching, localization, severity, confusion matrix)
# ---------------------------------------------------------------------------

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]


def category_group(category: str | None) -> str:
    if category in SECURITY:
        return "security"
    if category in CORRECTNESS:
        return "bug"
    if category in QUALITY:
        return "quality"
    return "none"


def _safe_div(a: float, b: float) -> float | None:
    return round(a / b, 4) if b else None


def _f1(p: float | None, r: float | None) -> float | None:
    if p is None or r is None or (p + r) == 0:
        return None if p is None or r is None else 0.0
    return round(2 * p * r / (p + r), 4)


def match_case(predicted: list[dict], expected: list[dict], tolerance: int = 2) -> dict:
    """Match one case's predictions to its expected defects.

    A prediction *detects* an expected defect when it is in the same file and its
    line range is within `tolerance` lines of the defect line (category ignored).
    It is a *strict* (fully correct) detection only if the category group also
    matches. Each expected defect is matched at most once; a second prediction for
    an already-matched defect is a duplicate, and any other prediction is a false
    positive.
    """
    used: set[int] = set()
    matches = []
    for e in expected:
        candidates = [i for i, p in enumerate(predicted) if i not in used and p["file"] == e["file"]
                      and p["start_line"] - tolerance <= e["line"] <= p["end_line"] + tolerance]
        # Prefer a category-correct prediction, then the tightest line range.
        candidates.sort(key=lambda i: (category_group(predicted[i]["category"]) != category_group(e["category"]),
                                       predicted[i]["end_line"] - predicted[i]["start_line"]))
        if not candidates:
            matches.append({"expected": e, "prediction": None})
            continue
        i = candidates[0]
        used.add(i)
        matches.append({"expected": e, "prediction": predicted[i]})
    duplicates, false_positives = [], []
    for i, p in enumerate(predicted):
        if i in used:
            continue
        near_matched = any(m["prediction"] is not None and m["expected"]["file"] == p["file"]
                           and p["start_line"] - tolerance <= m["expected"]["line"] <= p["end_line"] + tolerance
                           for m in matches)
        (duplicates if near_matched else false_positives).append(p)
    return {"matches": matches, "duplicates": duplicates, "false_positives": false_positives}


def score_review_cases(cases: list[dict], tolerance: int = 2) -> dict:
    """Aggregate metrics over cases: [{"id", "predicted": [...], "expected": [...]}, ...].

    Definitions (E = expected defects, P = predictions):
      detected        expected defect matched by file + line (+/- tolerance), any category
      strict TP       detected AND category group correct (security / bug / quality)
      precision       strict TP / P          (lenient: detected / P)
      recall          strict TP / E          (lenient: detected / E)
      F1              harmonic mean of precision and recall
      FNR             1 - recall (strict)
      clean FP rate   clean cases with >= 1 prediction / clean cases
      category acc.   strict TP / detected
      severity        exact (and within one level) agreement among detected
      line exact      expected line inside the predicted range, among detected
      duplicate rate  duplicate predictions / P
    """
    E = P = detected = strict = dup = fp = 0
    sev_exact = sev_near = line_exact = 0
    clean = clean_flagged = 0
    by_type: dict[str, dict] = {}
    groups = ["security", "bug", "quality", "none"]
    confusion = {row: dict.fromkeys(groups, 0) for row in ("security", "bug", "clean")}
    per_case = []
    for case in cases:
        result = match_case(case["predicted"], case["expected"], tolerance)
        E += len(case["expected"])
        P += len(case["predicted"])
        dup += len(result["duplicates"])
        fp += len(result["false_positives"])
        if not case["expected"]:
            clean += 1
            clean_flagged += bool(case["predicted"])
            if case["predicted"]:
                for p in case["predicted"]:
                    confusion["clean"][category_group(p["category"])] += 1
            else:
                confusion["clean"]["none"] += 1
        case_detected = case_strict = 0
        for m in result["matches"]:
            e, p = m["expected"], m["prediction"]
            row = category_group(e["category"])
            t = by_type.setdefault(e.get("type", e["category"]), {"expected": 0, "detected": 0, "strict": 0})
            t["expected"] += 1
            if p is None:
                confusion[row]["none"] += 1
                continue
            confusion[row][category_group(p["category"])] += 1
            detected += 1
            case_detected += 1
            t["detected"] += 1
            if category_group(p["category"]) == row:
                strict += 1
                case_strict += 1
                t["strict"] += 1
            if p.get("severity") and e.get("severity"):
                diff = abs(SEVERITY_ORDER.index(p["severity"]) - SEVERITY_ORDER.index(e["severity"]))
                sev_exact += diff == 0
                sev_near += diff <= 1
            line_exact += p["start_line"] <= e["line"] <= p["end_line"]
        per_case.append({"id": case["id"], "expected": len(case["expected"]), "predicted": len(case["predicted"]),
                         "detected": case_detected, "strict": case_strict,
                         "duplicates": len(result["duplicates"]), "false_positives": len(result["false_positives"])})

    precision, recall = _safe_div(strict, P), _safe_div(strict, E)
    lenient_p, lenient_r = _safe_div(detected, P), _safe_div(detected, E)
    return {
        "expected": E, "predicted": P, "detected": detected, "true_positives": strict,
        "false_positives": fp, "duplicates": dup,
        "precision": precision, "recall": recall, "f1": _f1(precision, recall),
        "lenient_precision": lenient_p, "lenient_recall": lenient_r, "lenient_f1": _f1(lenient_p, lenient_r),
        "false_negative_rate": round(1 - recall, 4) if recall is not None else None,
        "clean_cases": clean, "clean_case_false_positive_rate": _safe_div(clean_flagged, clean),
        "category_accuracy": _safe_div(strict, detected),
        "severity_exact": _safe_div(sev_exact, detected), "severity_within_one": _safe_div(sev_near, detected),
        "line_exact": _safe_div(line_exact, detected), "line_tolerance": tolerance,
        "duplicate_rate": _safe_div(dup, P),
        "by_type": {k: {**v, "recall": _safe_div(v["strict"], v["expected"]),
                        "detection_rate": _safe_div(v["detected"], v["expected"])} for k, v in sorted(by_type.items())},
        "confusion_matrix": {"rows": ["security", "bug", "clean"], "columns": groups, "counts": confusion},
        "per_case": per_case,
    }


def ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """Binary-relevance NDCG@k."""
    import math
    dcg = sum(1.0 / math.log2(i + 2) for i, item in enumerate(ranked[:k]) if item in relevant)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(k, len(relevant))))
    return dcg / ideal if ideal else 0.0
