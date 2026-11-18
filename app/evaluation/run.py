"""
Reproducible benchmarks for CodeLens CI.

Review benchmark (benchmarks/review_benchmark.json):

  python -m app.evaluation.run review --config all --split holdout
  python -m app.evaluation.run review --config llm_context --split validation --repeats 3

  configs:  rules        deterministic security rules only (no LLM, no context)
            llm          Gemini review of the diff, no repository context
            llm_context  Gemini review + hybrid retrieval of repository context (CodeLens)
            all          the three above, one after another (same cases, same model)

Retrieval benchmark (benchmarks/retrieval_benchmark.json):

  python -m app.evaluation.run retrieval --split holdout
  python -m app.evaluation.run retrieval-sweep              (validation split only)

Every run writes benchmarks/results/<run_id>.json (configuration, per-case
predictions, raw model output, errors, latency, token usage), <run_id>-cases.csv
and appends one row per configuration to benchmarks/results/summary.csv.

Rules for honest numbers (also enforced in code where possible):
  * Labels are never changed after looking at predictions.
  * Tuning (prompts, retrieval parameters) uses dev/validation only; the
    retrieval sweep refuses the holdout split. Headline results are holdout.
  * Modes that cannot run (no API key) are reported as skipped, never estimated.
"""
import argparse
import csv
import difflib
import hashlib
import json
import platform
import statistics
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.core.bm25 import BM25Index, tokenize
from app.core.chunker import chunk_repository
from app.core.diff_parser import parse_pull_request_files
from app.core.retriever import RRF_K, HybridRetriever, bm25_document
from app.evaluation.metrics import (ndcg_at_k, precision_at_k, reciprocal_rank, recall_at_k, review_scores,
                                    score_review_cases, summarize_latencies)

ROOT = Path(__file__).resolve().parents[2] / "benchmarks"
SAMPLE_REPO = ROOT / "sample_repo"
RESULTS = ROOT / "results"
BENCH_REPO_ID = 1          # collection name used for the in-memory benchmark index
REVIEW_CONFIGS = ("rules", "llm", "llm_context")
SPLITS = ("dev", "validation", "holdout", "all")


# ---------------------------------------------------------------- shared helpers

def _label(payload: dict) -> str:
    return f"{payload['file']}::{payload['qualified_name']}"


def _unique(labels: list[str]) -> list[str]:
    return list(dict.fromkeys(labels))


def _sha(path_or_text) -> str:
    data = path_or_text.read_bytes() if isinstance(path_or_text, Path) else str(path_or_text).encode()
    return hashlib.sha256(data).hexdigest()[:12]


def _prompt_version() -> str:
    from app.review.prompts import FOCUS, SYSTEM_PROMPT
    return _sha(SYSTEM_PROMPT + json.dumps(FOCUS, sort_keys=True))


def _now_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"


def _environment() -> dict:
    from app.review.llm import GENERATION_CONFIG
    return {"timestamp": datetime.now(UTC).isoformat(), "python": platform.python_version(),
            "embedding_model": "gemini-embedding-001", "dimensions": 1536,
            "query_task_type": settings.EMBEDDING_QUERY_TASK_TYPE, "review_model": settings.GEMINI_REVIEW_MODEL,
            "generation_config": GENERATION_CONFIG, "prompt_version": _prompt_version(),
            "retrieval": {"top_k": settings.REVIEW_TOP_K, "rrf_k": RRF_K}}


def _write(report: dict, name: str) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    return path


def _append_summary(row: dict) -> None:
    path = RESULTS / "summary.csv"
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        if new:
            writer.writeheader()
        writer.writerow(row)


def _in_split(case: dict, split: str) -> bool:
    return split == "all" or case.get("split", "dev") == split


CACHE = ROOT / ".cache"
BENCH_COLLECTION_ID = 900_000_002   # dedicated Qdrant collection for the benchmark corpus


class QueryVectorCache(dict):
    """Query embeddings persisted on disk, keyed by model + task type + text.

    Free-tier embedding quota is 1000 requests/day; benchmark queries are fixed,
    so each one is embedded once, ever.
    """

    def __init__(self):
        super().__init__()
        self.prefix = f"gemini-embedding-001|1536|{settings.EMBEDDING_QUERY_TASK_TYPE}|"
        self.path = CACHE / "query_vectors.json"
        if self.path.exists():
            try:
                stored = json.loads(self.path.read_text())
                super().update({k[len(self.prefix):]: v for k, v in stored.items() if k.startswith(self.prefix)})
            except (OSError, ValueError):
                pass

    def save(self) -> None:
        CACHE.mkdir(parents=True, exist_ok=True)
        stored = {}
        if self.path.exists():
            try:
                stored = json.loads(self.path.read_text())
            except (OSError, ValueError):
                stored = {}
        stored.update({self.prefix + k: v for k, v in self.items()})
        self.path.write_text(json.dumps(stored))


def _index_sample_repo(cache: "QueryVectorCache | None" = None):
    """Index the sample repository once, in the configured Qdrant if reachable.

    Re-runs are incremental (unchanged chunks keep their vectors), so the
    benchmark corpus costs embedding quota only the first time.
    """
    from qdrant_client import QdrantClient

    from app.core.embedder import get_qdrant_client, index_repository
    try:
        qdrant = get_qdrant_client()
        qdrant.get_collections()
    except Exception:  # no Qdrant server: fall back to memory (re-embeds every run)
        qdrant = QdrantClient(":memory:")
    started = time.perf_counter()
    index_repository(BENCH_COLLECTION_ID, chunk_repository(SAMPLE_REPO), qdrant=qdrant)
    return (HybridRetriever(BENCH_COLLECTION_ID, qdrant, query_cache=cache),
            round(time.perf_counter() - started, 3))


# ---------------------------------------------------------------- retrieval

def run_retrieval(modes: list[str], k: int, split: str = "all", rrf_k: int = RRF_K,
                  weights: tuple[float, float] = (1.0, 1.0), candidate_k: int | None = None) -> dict:
    dataset_path = ROOT / "retrieval_benchmark.json"
    dataset = json.loads(dataset_path.read_text())
    cases = [c for c in dataset["cases"] if _in_split(c, split)]
    chunks = chunk_repository(SAMPLE_REPO)
    payloads = [c.to_dict() for c in chunks]
    run_id = _now_id("retrieval")
    report = {"benchmark": "retrieval", "measured": True, "run_id": run_id, "split": split, "k": k,
              "dataset_version": _sha(dataset_path), "corpus_chunks": len(chunks), "queries": len(cases),
              "params": {"rrf_k": rrf_k, "weights_dense_bm25": list(weights), "candidate_k": candidate_k},
              "environment": _environment(), "modes": {}}

    retriever = None
    if any(m in ("dense", "hybrid") for m in modes):
        if not settings.GEMINI_API_KEY:
            for mode in modes:
                if mode != "bm25":
                    report["modes"][mode] = {"skipped": "GEMINI_API_KEY not set"}
        else:
            cache = QueryVectorCache()
            retriever, report["index_seconds"] = _index_sample_repo(cache)

    bm25 = BM25Index([bm25_document(p) for p in payloads])
    vectors: dict[str, list[float]] = {}
    for mode in modes:
        if mode in report["modes"]:
            continue
        per_query, latencies = [], []
        for case in cases:
            relevant = set(case["relevant"])
            started = time.perf_counter()
            if mode == "bm25":
                scores = bm25.scores(tokenize(case["query"]))
                order = sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: -scores[i])
                ranked = _unique([_label(payloads[i]) for i in order])
            else:
                if case["id"] not in vectors:
                    vectors[case["id"]] = retriever.embed(case["query"])   # cached on disk
                result = retriever.search(case["query"], top_k=max(k, 10), mode=mode, rrf_k=rrf_k,
                                          weights=weights, candidate_k=candidate_k,
                                          query_vector=vectors[case["id"]])
                ranked = _unique([_label(c.payload) for c in result.chunks])
            latencies.append((time.perf_counter() - started) * 1000)
            per_query.append({"id": case["id"], "split": case.get("split"), "kind": case.get("kind"),
                              "top5": ranked[:5], "relevant": sorted(relevant),
                              "p@1": precision_at_k(ranked, relevant, 1), "p@3": precision_at_k(ranked, relevant, 3),
                              f"p@{k}": precision_at_k(ranked, relevant, k), f"r@{k}": recall_at_k(ranked, relevant, k),
                              f"ndcg@{k}": round(ndcg_at_k(ranked, relevant, k), 4),
                              "rr": reciprocal_rank(ranked, relevant)})
        n = len(per_query) or 1
        mean = lambda key: round(sum(q[key] for q in per_query) / n, 4)  # noqa: E731
        report["modes"][mode] = {
            "precision@1": mean("p@1"), "precision@3": mean("p@3"), f"precision@{k}": mean(f"p@{k}"),
            f"recall@{k}": mean(f"r@{k}"), f"ndcg@{k}": mean(f"ndcg@{k}"), "mrr": mean("rr"),
            "latency": summarize_latencies(latencies), "per_query": per_query,
        }
    if retriever is not None and retriever.query_cache is not None:
        retriever.query_cache.save()
    for mode, m in report["modes"].items():
        if "skipped" not in m:
            _append_summary({"run_id": run_id, "benchmark": "retrieval", "config": mode, "split": split,
                             "n": len(cases), "p@1": m["precision@1"], f"p@{k}": m[f"precision@{k}"],
                             f"r@{k}": m[f"recall@{k}"], f"ndcg@{k}": m[f"ndcg@{k}"], "mrr": m["mrr"],
                             "params": json.dumps(report["params"]), "dataset_version": report["dataset_version"]})
    return report


def run_retrieval_sweep(k: int = 5) -> dict:
    """Grid over RRF parameters on the VALIDATION split only (never holdout)."""
    if not settings.GEMINI_API_KEY:
        return {"skipped": "GEMINI_API_KEY not set"}
    grid = [(rrf_k, weights, cand) for rrf_k in (10, 30, 60) for weights in ((1.0, 1.0), (1.0, 2.0), (2.0, 1.0))
            for cand in (20, 50)]
    rows = []
    for rrf_k, weights, cand in grid:
        report = run_retrieval(["hybrid"], k, "validation", rrf_k, weights, cand)
        m = report["modes"]["hybrid"]
        rows.append({"rrf_k": rrf_k, "weights_dense_bm25": weights, "candidate_k": cand,
                     "mrr": m["mrr"], f"ndcg@{k}": m[f"ndcg@{k}"], "precision@1": m["precision@1"]})
    rows.sort(key=lambda r: (-r[f"ndcg@{k}"], -r["mrr"]))
    return {"benchmark": "retrieval-sweep", "split": "validation", "measured": True, "results": rows,
            "note": "Choose parameters from this table, then evaluate them ONCE on holdout."}


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
        out.append({"file": case["file"], "line": line, "category": item["category"],
                    "type": item.get("type", item["category"]), "severity": item.get("severity")})
    return out


def _run_config(config: str, cases: list[dict], repeats: int, retriever, llm) -> list[dict]:
    from app.review.graph import ReviewPipeline
    pipeline = ReviewPipeline(llm if config != "rules" else None,
                              retriever if config == "llm_context" else None, top_k=settings.REVIEW_TOP_K)
    runs = []
    for repeat in range(repeats):
        results = []
        for case in cases:
            base, new = _apply_case(case)
            files = parse_pull_request_files([{"filename": case["file"], "status": "modified",
                                               "patch": _github_patch(base, new)}])
            started = time.perf_counter()
            state = pipeline.run({"repo_full_name": "bench/sample", "pr_number": 0, "pr_title": case["title"],
                                  "pr_body": "", "files": files, "file_contents": {case["file"]: new}})
            calls = state.get("llm_calls", [])
            results.append({
                "id": case["id"], "split": case.get("split"), "repeat": repeat,
                "expected": _expected_lines(case, new),
                "predicted": [{"file": f.file, "start_line": f.start_line, "end_line": f.end_line,
                               "category": f.category, "severity": f.severity, "status": f.status,
                               "confidence": f.confidence, "title": f.title, "source": f.source}
                              for f in state["findings"]],
                "raw_model_findings": [f.model_dump() for f in state.get("raw_findings", [])
                                       if not f.source.startswith("rule:")],
                "raw_model_output": [c.get("raw_output") for c in calls],
                "dropped": state.get("dropped", []),
                "errors": state.get("errors", []),
                "llm_calls": [{k: c.get(k) for k in ("focus", "ok", "error", "input_tokens", "output_tokens",
                                                     "latency_ms", "attempts")} for c in calls],
                "api_failures": sum(1 for c in calls if not c.get("ok")),
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "risk": state["risk"]["label"],
            })
        runs.append(results)
    return runs


def _confirmed_only(results: list[dict]) -> list[dict]:
    return [{**r, "predicted": [p for p in r["predicted"] if p["status"] == "confirmed"]} for r in results]


def run_review(config: str, split: str = "holdout", repeats: int = 1) -> dict:
    """Run one review configuration and return its report (also written to disk)."""
    dataset_path = ROOT / "review_benchmark.json"
    dataset = json.loads(dataset_path.read_text())
    cases = [c for c in dataset["cases"] if _in_split(c, split)]
    run_id = _now_id(f"review-{config}-{split}")
    report = {"benchmark": "review", "measured": True, "run_id": run_id, "mode": config, "config": config,
              "split": split, "repeats": repeats, "dataset_version": _sha(dataset_path),
              "dataset_schema": dataset.get("version", "1"), "environment": _environment(), "n_cases": len(cases)}
    if config == "rules":
        report["environment"]["review_model"] = None

    llm = retriever = None
    if config in ("llm", "llm_context"):
        if not settings.GEMINI_API_KEY:
            return {**report, "skipped": "GEMINI_API_KEY not set"}
        from app.review.llm import GeminiReviewLLM
        llm = GeminiReviewLLM()
        if config == "llm_context":
            cache = QueryVectorCache()
            retriever, report["index_seconds"] = _index_sample_repo(cache)

    runs = _run_config(config, cases, repeats, retriever, llm)
    if retriever is not None:
        retriever.query_cache.save()
    per_run = [score_review_cases(_confirmed_only(r)) for r in runs]
    report["cases"] = runs[0] if repeats == 1 else [c for run in runs for c in run]
    report["confirmed_only"] = per_run[0] if repeats == 1 else _mean_metrics(per_run)
    report["including_possible"] = score_review_cases(runs[0])
    report["legacy_scores"] = review_scores(_confirmed_only(runs[0]))     # v1 metric, for comparison
    if repeats > 1:
        f1s = [m["f1"] or 0.0 for m in per_run]
        report["variability"] = {
            "f1_per_run": f1s, "f1_mean": round(statistics.mean(f1s), 4),
            "f1_stdev": round(statistics.stdev(f1s), 4) if len(f1s) > 1 else 0.0,
            "case_agreement": _case_agreement(runs),
        }
    all_cases = [c for run in runs for c in run]
    tokens = [c.get("input_tokens", 0) + c.get("output_tokens", 0) for r in all_cases for c in r["llm_calls"]
              if c.get("ok") and c.get("input_tokens") is not None]
    report["latency"] = summarize_latencies([r["latency_ms"] for r in all_cases])
    report["tokens"] = {"total": sum(tokens), "per_case_mean": round(sum(tokens) / len(all_cases), 1) if all_cases else 0,
                        "reported_calls": len(tokens)}
    report["api_failures"] = sum(r["api_failures"] for r in all_cases)
    # Retrieval that fell back to BM25 (e.g. embedding quota) is NOT the llm_context
    # configuration; the run is flagged so it can never pass as a valid result.
    report["retrieval_degraded_cases"] = sum(1 for r in runs[0] if any("degraded" in e for e in r["errors"]))
    report["valid"] = report["api_failures"] == 0 and report["retrieval_degraded_cases"] == 0
    report["cases_with_errors"] = sum(1 for r in runs[0] if r["api_failures"])

    m = report["confirmed_only"]
    _append_summary({"valid": report["valid"], "run_id": run_id, "benchmark": "review", "config": config, "split": split,
                     "n": len(cases), "repeats": repeats, "precision": m["precision"], "recall": m["recall"],
                     "f1": m["f1"], "clean_fp_rate": m["clean_case_false_positive_rate"],
                     "category_accuracy": m["category_accuracy"], "line_exact": m["line_exact"],
                     "model": report["environment"]["review_model"], "prompt_version": report["environment"]["prompt_version"],
                     "dataset_version": report["dataset_version"], "api_failures": report["api_failures"]})
    _write_cases_csv(run_id, runs)
    return report


def _mean_metrics(per_run: list[dict]) -> dict:
    keys = ("precision", "recall", "f1", "lenient_precision", "lenient_recall", "lenient_f1", "false_negative_rate",
            "clean_case_false_positive_rate", "category_accuracy", "severity_exact", "severity_within_one",
            "line_exact", "duplicate_rate")
    mean = dict(per_run[0])
    for key in keys:
        values = [m[key] for m in per_run if m[key] is not None]
        mean[key] = round(statistics.mean(values), 4) if values else None
    mean["note"] = f"mean over {len(per_run)} runs; counts and matrices are from run 1"
    return mean


def _case_agreement(runs: list[list[dict]]) -> dict:
    """Per case: fraction of runs whose confirmed findings fully detect every expected defect."""
    out = {}
    for i, case in enumerate(runs[0]):
        hits = 0
        for run in runs:
            scored = score_review_cases(_confirmed_only([run[i]]))
            hits += (scored["true_positives"] == scored["expected"] and scored["false_positives"] == 0)
        out[case["id"]] = round(hits / len(runs), 3)
    return out


def _write_cases_csv(run_id: str, runs: list[list[dict]]) -> None:
    path = RESULTS / f"{run_id}-cases.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["run_id", "repeat", "case", "split", "expected", "predicted_confirmed", "strict_tp",
                         "false_positives", "duplicates", "api_failures", "latency_ms", "risk"])
        for run in runs:
            for r in run:
                s = score_review_cases(_confirmed_only([r]))
                writer.writerow([run_id, r["repeat"], r["id"], r["split"],
                                 ";".join(f"{e['type']}@{e['line']}" for e in r["expected"]) or "clean",
                                 ";".join(f"{p['category']}@{p['start_line']}" for p in r["predicted"]
                                          if p["status"] == "confirmed"),
                                 s["true_positives"], s["false_positives"], s["duplicates"],
                                 r["api_failures"], r["latency_ms"], r["risk"]])


# ---------------------------------------------------------------- CLI

def _print_retrieval(report: dict) -> None:
    k = report["k"]
    print(f"Retrieval benchmark [{report['split']}]: {report['queries']} queries, {report['corpus_chunks']} chunks")
    print(f"{'mode':8} {'P@1':>6} {'P@'+str(k):>6} {'R@'+str(k):>6} {'NDCG':>6} {'MRR':>6} {'median ms':>10}")
    for mode, m in report["modes"].items():
        if "skipped" in m:
            print(f"{mode:8} skipped: {m['skipped']}")
            continue
        print(f"{mode:8} {m['precision@1']:6.3f} {m[f'precision@{k}']:6.3f} {m[f'recall@{k}']:6.3f} "
              f"{m[f'ndcg@{k}']:6.3f} {m['mrr']:6.3f} {m['latency']['median_ms']:10.2f}")


def _print_review(report: dict) -> None:
    if "skipped" in report:
        print(f"{report['config']:12} skipped: {report['skipped']}")
        return
    m = report["confirmed_only"]
    fmt = lambda v: "  -  " if v is None else f"{v:5.3f}"  # noqa: E731
    print(f"{report['config']:12} P {fmt(m['precision'])}  R {fmt(m['recall'])}  F1 {fmt(m['f1'])}  "
          f"cleanFP {fmt(m['clean_case_false_positive_rate'])}  cat {fmt(m['category_accuracy'])}  "
          f"line {fmt(m['line_exact'])}  dup {fmt(m['duplicate_rate'])}  api_fail {report['api_failures']}")
    if not report.get("valid", True):
        print(f"  !! INVALID RUN: {report['api_failures']} LLM failure(s), "
              f"{report['retrieval_degraded_cases']} case(s) with degraded retrieval (quota?). "
              "Do not report these numbers; re-run when the quota resets.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.evaluation.run")
    sub = parser.add_subparsers(dest="benchmark", required=True)
    r = sub.add_parser("retrieval")
    r.add_argument("--modes", default="bm25,dense,hybrid")
    r.add_argument("--k", type=int, default=5)
    r.add_argument("--split", choices=SPLITS, default="all")
    r.add_argument("--rrf-k", type=int, default=RRF_K)
    r.add_argument("--weights", default="1,1", help="dense,bm25 RRF weights")
    sub.add_parser("retrieval-sweep", help="RRF parameter grid on the validation split")
    v = sub.add_parser("review")
    v.add_argument("--config", choices=(*REVIEW_CONFIGS, "all"), default="all")
    v.add_argument("--mode", choices=("rules", "llm"), help="deprecated alias: rules -> rules, llm -> llm_context")
    v.add_argument("--split", choices=SPLITS, default="holdout")
    v.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()

    if args.benchmark == "retrieval":
        weights = tuple(float(x) for x in args.weights.split(","))
        report = run_retrieval([m.strip() for m in args.modes.split(",")], args.k, args.split, args.rrf_k, weights)
        _print_retrieval(report)
        print("report:", _write(report, report["run_id"]))
    elif args.benchmark == "retrieval-sweep":
        report = run_retrieval_sweep()
        if "skipped" in report:
            print("sweep skipped:", report["skipped"])
            return
        for row in report["results"][:10]:
            print(row)
        print("report:", _write(report, _now_id("retrieval-sweep")))
    else:
        configs = list(REVIEW_CONFIGS) if args.config == "all" else [args.config]
        if args.mode:
            configs = ["rules" if args.mode == "rules" else "llm_context"]
        print(f"Review benchmark, split={args.split}, repeats={args.repeats}")
        for config in configs:
            report = run_review(config, args.split, args.repeats)
            _print_review(report)
            if "skipped" not in report:
                print("  report:", _write(report, report["run_id"]))


if __name__ == "__main__":
    main()
