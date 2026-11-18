"""
Reproducible benchmarks.

  python -m app.evaluation.run retrieval [--modes bm25,dense,hybrid] [--k 5]
  python -m app.evaluation.run review [--mode rules|llm]

Retrieval: queries in benchmarks/retrieval_benchmark.json against the sample
repository; metrics Precision@1/3/5, Recall@5, MRR and latency per mode.
"bm25" needs nothing external. "dense"/"hybrid" call the real Gemini API
(free tier) and use an in-memory Qdrant.

Review: seeded-defect PRs in benchmarks/review_benchmark.json. "rules" runs
the pipeline with no LLM (deterministic security rules only); "llm" runs the
full pipeline with the real Gemini review model and retrieval context.

Every report is written to benchmarks/results/ with `measured: true` and the
exact configuration. Nothing here fabricates numbers: a mode that cannot run
(e.g. no API key) is reported as skipped with the reason.
"""
import argparse
import difflib
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.core.bm25 import BM25Index, tokenize
from app.core.chunker import chunk_repository
from app.core.diff_parser import parse_pull_request_files
from app.core.retriever import HybridRetriever, bm25_document
from app.evaluation.metrics import (precision_at_k, reciprocal_rank, recall_at_k, review_scores,
                                    summarize_latencies)

ROOT = Path(__file__).resolve().parents[2] / "benchmarks"
SAMPLE_REPO = ROOT / "sample_repo"
RESULTS = ROOT / "results"
BENCH_REPO_ID = 1          # collection name used for the in-memory benchmark index


def _label(payload: dict) -> str:
    return f"{payload['file']}::{payload['qualified_name']}"


def _unique(labels: list[str]) -> list[str]:
    return list(dict.fromkeys(labels))


def _write(report: dict, name: str) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{name}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(report, indent=2))
    return path


def _environment() -> dict:
    return {"timestamp": datetime.now(UTC).isoformat(), "python": platform.python_version(),
            "embedding_model": "gemini-embedding-001", "dimensions": 1536,
            "query_task_type": settings.EMBEDDING_QUERY_TASK_TYPE, "review_model": settings.GEMINI_REVIEW_MODEL}


# ---------------------------------------------------------------- retrieval

def run_retrieval(modes: list[str], k: int) -> dict:
    dataset = json.loads((ROOT / "retrieval_benchmark.json").read_text())
    chunks = chunk_repository(SAMPLE_REPO)
    payloads = [c.to_dict() for c in chunks]
    report = {"benchmark": "retrieval", "measured": True, "k": k, "corpus_chunks": len(chunks),
              "queries": len(dataset["cases"]), "environment": _environment(), "modes": {}}

    retriever = None
    index_seconds = None
    if any(m in ("dense", "hybrid") for m in modes):
        if not settings.GEMINI_API_KEY:
            for mode in modes:
                if mode != "bm25":
                    report["modes"][mode] = {"skipped": "GEMINI_API_KEY not set"}
        else:
            from qdrant_client import QdrantClient

            from app.core.embedder import index_repository
            qdrant = QdrantClient(":memory:")
            started = time.perf_counter()
            index_repository(BENCH_REPO_ID, chunks, qdrant=qdrant)
            index_seconds = round(time.perf_counter() - started, 3)
            retriever = HybridRetriever(BENCH_REPO_ID, qdrant)
    report["index_seconds"] = index_seconds

    bm25 = BM25Index([bm25_document(p) for p in payloads])
    for mode in modes:
        if mode in report["modes"]:
            continue
        per_query, latencies = [], []
        for case in dataset["cases"]:
            relevant = set(case["relevant"])
            started = time.perf_counter()
            if mode == "bm25":
                scores = bm25.scores(tokenize(case["query"]))
                order = sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: -scores[i])
                ranked = _unique([_label(payloads[i]) for i in order])
            else:
                result = retriever.search(case["query"], top_k=max(k, 10), mode=mode)
                ranked = _unique([_label(c.payload) for c in result.chunks])
            latencies.append((time.perf_counter() - started) * 1000)
            per_query.append({"id": case["id"], "top5": ranked[:5],
                              "p@1": precision_at_k(ranked, relevant, 1),
                              "p@3": precision_at_k(ranked, relevant, 3),
                              f"p@{k}": precision_at_k(ranked, relevant, k),
                              f"r@{k}": recall_at_k(ranked, relevant, k),
                              "rr": reciprocal_rank(ranked, relevant)})
        n = len(per_query)
        report["modes"][mode] = {
            "precision@1": round(sum(q["p@1"] for q in per_query) / n, 4),
            "precision@3": round(sum(q["p@3"] for q in per_query) / n, 4),
            f"precision@{k}": round(sum(q[f"p@{k}"] for q in per_query) / n, 4),
            f"recall@{k}": round(sum(q[f"r@{k}"] for q in per_query) / n, 4),
            "mrr": round(sum(q["rr"] for q in per_query) / n, 4),
            "latency": summarize_latencies(latencies),
            "per_query": per_query,
        }
    return report


# ---------------------------------------------------------------- review

def _apply_case(case: dict) -> tuple[str, str]:
    base = (SAMPLE_REPO / case["file"]).read_text()
    new = base
    for old, replacement in case.get("replace", []):
        if old not in new:
            raise ValueError(f"case {case['id']}: text to replace not found in {case['file']}")
        new = new.replace(old, replacement, 1)
    new += case.get("append", "")
    return base, new


def _github_patch(base: str, new: str) -> str:
    diff = list(difflib.unified_diff(base.splitlines(), new.splitlines(), lineterm="", n=3))
    return "\n".join(diff[2:])   # drop ---/+++ headers, like GitHub's `patch` field


def _expected_lines(case: dict, new: str) -> list[dict]:
    lines = new.split("\n")
    out = []
    for item in case["expected"]:
        needle = item["match"].rstrip("\n")
        line = next(i for i, text in enumerate(lines, 1) if needle in text)
        out.append({"file": case["file"], "line": line, "category": item["category"]})
    return out


def run_review(mode: str) -> dict:
    dataset = json.loads((ROOT / "review_benchmark.json").read_text())
    report = {"benchmark": "review", "measured": True, "mode": mode, "environment": _environment(),
              "cases": []}
    llm = retriever = None
    if mode == "llm":
        if not settings.GEMINI_API_KEY:
            return {**report, "skipped": "GEMINI_API_KEY not set"}
        from qdrant_client import QdrantClient

        from app.core.embedder import index_repository
        from app.review.llm import GeminiReviewLLM
        qdrant = QdrantClient(":memory:")
        index_repository(BENCH_REPO_ID, chunk_repository(SAMPLE_REPO), qdrant=qdrant)
        retriever = HybridRetriever(BENCH_REPO_ID, qdrant)
        llm = GeminiReviewLLM()

    from app.review.graph import ReviewPipeline
    pipeline = ReviewPipeline(llm, retriever, top_k=settings.REVIEW_TOP_K)
    scored_confirmed, scored_all, latencies = [], [], []
    for case in dataset["cases"]:
        base, new = _apply_case(case)
        files = parse_pull_request_files([{"filename": case["file"], "status": "modified",
                                           "patch": _github_patch(base, new)}])
        started = time.perf_counter()
        state = pipeline.run({"repo_full_name": "bench/sample", "pr_number": 0, "pr_title": case["title"],
                              "pr_body": "", "files": files, "file_contents": {case["file"]: new}})
        latencies.append((time.perf_counter() - started) * 1000)
        expected = _expected_lines(case, new)
        predicted_all = [{"file": f.file, "start_line": f.start_line, "end_line": f.end_line,
                          "category": f.category, "status": f.status, "title": f.title, "source": f.source}
                         for f in state["findings"]]
        confirmed = [p for p in predicted_all if p["status"] == "confirmed"]
        scored_confirmed.append({"predicted": confirmed, "expected": expected})
        scored_all.append({"predicted": predicted_all, "expected": expected})
        report["cases"].append({"id": case["id"], "expected": expected, "predicted": predicted_all,
                                "errors": state.get("errors", []), "latency_ms": round(latencies[-1], 1),
                                "risk": state["risk"]["label"]})
    report["confirmed_only"] = review_scores(scored_confirmed)
    report["including_possible"] = review_scores(scored_all)
    report["latency"] = summarize_latencies(latencies)
    return report


def _print_retrieval(report: dict) -> None:
    k = report["k"]
    print(f"Retrieval benchmark: {report['queries']} queries, {report['corpus_chunks']} chunks")
    print(f"{'mode':8} {'P@1':>6} {'P@3':>6} {'P@'+str(k):>6} {'R@'+str(k):>6} {'MRR':>6} {'median ms':>10}")
    for mode, m in report["modes"].items():
        if "skipped" in m:
            print(f"{mode:8} skipped: {m['skipped']}")
            continue
        print(f"{mode:8} {m['precision@1']:6.3f} {m['precision@3']:6.3f} {m[f'precision@{k}']:6.3f} "
              f"{m[f'recall@{k}']:6.3f} {m['mrr']:6.3f} {m['latency']['median_ms']:10.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.evaluation.run")
    sub = parser.add_subparsers(dest="benchmark", required=True)
    r = sub.add_parser("retrieval")
    r.add_argument("--modes", default="bm25,dense,hybrid")
    r.add_argument("--k", type=int, default=5)
    v = sub.add_parser("review")
    v.add_argument("--mode", choices=("rules", "llm"), default="llm")
    args = parser.parse_args()

    if args.benchmark == "retrieval":
        report = run_retrieval([m.strip() for m in args.modes.split(",")], args.k)
        _print_retrieval(report)
        print("report:", _write(report, "retrieval"))
    else:
        report = run_review(args.mode)
        if "skipped" in report:
            print("review benchmark skipped:", report["skipped"])
            return
        print(json.dumps({"mode": report["mode"], "confirmed_only": report["confirmed_only"],
                          "including_possible": report["including_possible"],
                          "latency": report["latency"]}, indent=2))
        print("report:", _write(report, f"review-{args.mode}"))


if __name__ == "__main__":
    main()
