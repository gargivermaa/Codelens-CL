"""
CodeLens CI command line (local demos and operations).

  python -m app.cli index-local PATH --github-id ID      index a local checkout
  python -m app.cli index OWNER/REPO [--ref REF]         index a GitHub repo (default branch)
  python -m app.cli retrieve --github-id ID "query"      hybrid retrieval
  python -m app.cli review OWNER/REPO PR [--no-post]     run a full review synchronously

`review` is exactly what the Celery worker runs, without the queue.
"""
import argparse
import json
import logging
import tempfile
from pathlib import Path

from app.logging_config import configure_logging


def _cmd_index_local(args):
    from app.core.chunker import chunk_repository
    from app.core.embedder import index_repository
    chunks = chunk_repository(args.path)
    result = index_repository(args.github_id, chunks,
                              progress=lambda stage, done, total: logging.info("%s %d/%d", stage, done, total))
    print(json.dumps(result.__dict__, indent=2))


def _cmd_index(args):
    from app.core.chunker import chunk_repository
    from app.core.embedder import index_repository
    from app.github.client import GitHubClient
    gh = GitHubClient.for_installation(args.installation_id)
    repo = gh._request("GET", f"/repos/{args.repo}").json()
    ref = args.ref or repo["default_branch"]
    with tempfile.TemporaryDirectory(prefix="codelens-") as tmp:
        files = gh.download_python_sources(args.repo, ref, Path(tmp))
        chunks = chunk_repository(tmp)
        result = index_repository(int(repo["id"]), chunks)
    print(json.dumps({"repo": args.repo, "github_id": repo["id"], "ref": ref, "files": files,
                      **result.__dict__}, indent=2))


def _cmd_retrieve(args):
    from app.core.retriever import HybridRetriever
    result = HybridRetriever(args.github_id).search(args.query, top_k=args.top_k, mode=args.mode)
    print(f"mode={result.mode} dense_ms={result.dense_latency_ms} bm25_ms={result.bm25_latency_ms}"
          + (f" DEGRADED: {result.degraded_reason}" if result.degraded_reason else ""))
    for i, chunk in enumerate(result.chunks, 1):
        print(f"{i}. {chunk.score:.4f} {chunk.payload.get('qualified_name')} {chunk.location} "
              f"(dense_rank={chunk.dense_rank}, bm25_rank={chunk.bm25_rank})")


def _cmd_review(args):
    from app.models.database import SessionLocal
    from app.services.deps import build_deps
    from app.services.review_service import run_review
    deps = build_deps(args.installation_id)
    repo = deps.github.get_pull(args.repo, args.pr)["base"]["repo"]
    db = SessionLocal()
    try:
        result = run_review(db, deps, int(repo["id"]), args.repo, args.pr,
                            installation_id=args.installation_id, post=not args.no_post)
    finally:
        db.close()
    print(json.dumps(result, indent=2, default=str))


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index-local", help="index a local checkout")
    p.add_argument("path")
    p.add_argument("--github-id", type=int, required=True)
    p.set_defaults(func=_cmd_index_local)

    p = sub.add_parser("index", help="index a GitHub repository")
    p.add_argument("repo")
    p.add_argument("--ref")
    p.add_argument("--installation-id", type=int)
    p.set_defaults(func=_cmd_index)

    p = sub.add_parser("retrieve", help="hybrid retrieval over an indexed repository")
    p.add_argument("query")
    p.add_argument("--github-id", type=int, required=True)
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--mode", choices=("hybrid", "dense", "bm25"), default="hybrid")
    p.set_defaults(func=_cmd_retrieve)

    p = sub.add_parser("review", help="review a pull request now (no queue)")
    p.add_argument("repo")
    p.add_argument("pr", type=int)
    p.add_argument("--installation-id", type=int)
    p.add_argument("--no-post", action="store_true", help="store the review but do not post it")
    p.set_defaults(func=_cmd_review)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
