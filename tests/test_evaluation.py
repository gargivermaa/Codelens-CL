"""Metric correctness and benchmark dataset integrity."""
import json

import pytest

from app.core.chunker import chunk_repository
from app.evaluation import run as bench
from app.evaluation.metrics import (match_findings, precision_at_k, recall_at_k, reciprocal_rank,
                                    review_scores, summarize_latencies)


def test_ranking_metrics():
    ranked = ["a", "x", "b", "y", "z"]
    assert precision_at_k(ranked, {"a", "b"}, 1) == 1.0
    assert precision_at_k(ranked, {"a", "b"}, 3) == pytest.approx(2 / 3)
    assert recall_at_k(ranked, {"a", "b", "c"}, 5) == pytest.approx(2 / 3)
    assert reciprocal_rank(ranked, {"b"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(ranked, {"q"}) == 0.0


def test_finding_matching_requires_file_line_and_compatible_category():
    expected = [{"file": "a.py", "line": 10, "category": "security"}]
    assert match_findings([{"file": "a.py", "start_line": 9, "end_line": 9, "category": "security"}], expected) == (1, 1)
    assert match_findings([{"file": "a.py", "start_line": 20, "end_line": 20, "category": "security"}], expected) == (0, 0)
    assert match_findings([{"file": "a.py", "start_line": 10, "end_line": 10, "category": "quality"}], expected) == (0, 0)
    assert match_findings([{"file": "b.py", "start_line": 10, "end_line": 10, "category": "security"}], expected) == (0, 0)
    # two predictions for one defect: only one can match
    two = [{"file": "a.py", "start_line": 10, "end_line": 10, "category": "security"}] * 2
    assert match_findings(two, expected) == (1, 1)


def test_review_scores_and_clean_case_false_positive_rate():
    hit = {"file": "a.py", "start_line": 3, "end_line": 3, "category": "bug"}
    scores = review_scores([
        {"predicted": [hit], "expected": [{"file": "a.py", "line": 3, "category": "bug"}]},
        {"predicted": [hit], "expected": []},
        {"predicted": [], "expected": []},
    ])
    assert scores["finding_precision"] == 0.5 and scores["finding_recall"] == 1.0
    assert scores["clean_case_false_positive_rate"] == 0.5


def test_latency_summary():
    s = summarize_latencies([1, 2, 3, 4, 100])
    assert s["median_ms"] == 3 and s["max_ms"] == 100 and s["n"] == 5


def test_retrieval_labels_exist_in_the_sample_repository():
    labels = {f"{c.file}::{c.qualified_name}" for c in chunk_repository(bench.SAMPLE_REPO)}
    dataset = json.loads((bench.ROOT / "retrieval_benchmark.json").read_text())
    for case in dataset["cases"]:
        assert set(case["relevant"]) <= labels, case["id"]


def test_review_cases_apply_cleanly_and_expected_lines_are_added_lines():
    from app.core.diff_parser import parse_pull_request_files
    dataset = json.loads((bench.ROOT / "review_benchmark.json").read_text())
    for case in dataset["cases"]:
        base, new = bench._apply_case(case)
        [changed] = parse_pull_request_files([{"filename": case["file"], "status": "modified",
                                               "patch": bench._github_patch(base, new)}])
        assert changed.added_lines, case["id"]
        for expected in bench._expected_lines(case, new):
            assert expected["line"] in changed.added_lines, case["id"]


def test_rules_only_review_benchmark_runs_offline(monkeypatch, tmp_path):
    monkeypatch.setattr(bench, "RESULTS", tmp_path)
    dataset = json.loads((bench.ROOT / "review_benchmark.json").read_text())
    report = bench.run_review("rules", split="all")
    assert report["measured"] and len(report["cases"]) == len(dataset["cases"])
    assert report["confirmed_only"]["clean_case_false_positive_rate"] == 0.0
    assert report["dataset_version"] and report["run_id"].startswith("review-rules-all")
    assert (tmp_path / "summary.csv").exists() and (tmp_path / f"{report['run_id']}-cases.csv").exists()


def test_splits_are_disjoint_and_llm_configs_skip_without_a_key(monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setattr(bench, "RESULTS", tmp_path)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    dataset = json.loads((bench.ROOT / "review_benchmark.json").read_text())
    ids = {split: {c["id"] for c in dataset["cases"] if c["split"] == split} for split in ("dev", "validation", "holdout")}
    assert not (ids["dev"] & ids["validation"]) and not (ids["validation"] & ids["holdout"])
    assert sum(map(len, ids.values())) == len(dataset["cases"])
    assert bench.run_review("llm", split="holdout")["skipped"] == "GEMINI_API_KEY not set"
    assert bench.run_retrieval_sweep()["skipped"] == "GEMINI_API_KEY not set"


def test_strict_scoring_does_not_count_a_wrong_category_as_correct():
    from app.evaluation.metrics import score_review_cases
    case = {"id": "x", "expected": [{"file": "f.py", "line": 5, "category": "security", "type": "idor",
                                     "severity": "high"}],
            "predicted": [{"file": "f.py", "start_line": 5, "end_line": 5, "category": "bug", "severity": "medium"}]}
    s = score_review_cases([case])
    assert s["detected"] == 1 and s["true_positives"] == 0
    assert s["recall"] == 0.0 and s["lenient_recall"] == 1.0 and s["category_accuracy"] == 0.0
    assert s["confusion_matrix"]["counts"]["security"]["bug"] == 1


def test_ndcg():
    from app.evaluation.metrics import ndcg_at_k
    assert ndcg_at_k(["a", "b"], {"a"}, 5) == 1.0
    assert 0 < ndcg_at_k(["x", "a"], {"a"}, 5) < 1
