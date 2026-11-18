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
