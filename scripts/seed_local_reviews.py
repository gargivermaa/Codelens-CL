"""
Run the REAL review service on the benchmark PRs from local files.

Use it to try the dashboard without GitHub, or for offline demos. Each case in
benchmarks/review_benchmark.json becomes a pull request of the local repository
"local/shopapp-benchmark". The pull request data comes from local files instead of
the GitHub API. Everything after that is the production code path: diff parsing,
indexing (if GEMINI_API_KEY is set), LangGraph review, risk scoring and
PostgreSQL persistence. Nothing is posted to GitHub.

  python scripts/seed_local_reviews.py            # all cases
  python scripts/seed_local_reviews.py r02 c01    # selected cases

Rows created here are labelled with the repository "local/shopapp-benchmark".
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.core.embedder import get_qdrant_client  # noqa: E402
from app.evaluation import run as bench  # noqa: E402
from app.models.database import SessionLocal  # noqa: E402
from app.services.review_service import ReviewDeps, run_review  # noqa: E402

REPO = "local/shopapp-benchmark"
REPO_ID = 900_000_001
CASES = {c["id"]: c for c in bench.json.loads((bench.ROOT / "review_benchmark.json").read_text())["cases"]}


class LocalPullSource:
    """Serves one benchmark case through the GitHub client interface used by run_review."""

    def __init__(self, case: dict):
        self.case = case
        self.base, self.new = bench._apply_case(case)

    def get_pull(self, repo, number):
        return {"title": self.case["title"], "body": "Benchmark case " + self.case["id"], "state": "open",
                "user": {"login": "benchmark"}, "head": {"sha": f"{self.case['id']:0<40}"[:40]},
                "base": {"sha": "b" * 40, "repo": {"default_branch": "main", "private": False}}}

    def list_pull_files(self, repo, number):
        return [{"filename": self.case["file"], "status": "modified", "patch": bench._github_patch(self.base, self.new)}]

    def get_file_content(self, repo, path, ref):
        return self.new if path == self.case["file"] else None

    def download_python_sources(self, repo, ref, dest: Path):
        shutil.copytree(bench.SAMPLE_REPO, dest, dirs_exist_ok=True)
        return sum(1 for _ in Path(dest).rglob("*.py"))

    def create_review(self, *args, **kwargs):
        raise RuntimeError("local source: posting is disabled")


def main(selected: list[str]) -> None:
    llm = gemini = None
    if settings.GEMINI_API_KEY:
        from app.core.embedder import get_gemini_client
        from app.review.llm import GeminiReviewLLM
        gemini = get_gemini_client()
        llm = GeminiReviewLLM(client=gemini)
    qdrant = get_qdrant_client()
    db = SessionLocal()
    try:
        for number, case_id in enumerate(selected or CASES, start=1):
            case = CASES[case_id]
            deps = ReviewDeps(github=LocalPullSource(case), qdrant=qdrant, llm=llm, gemini_client=gemini)
            result = run_review(db, deps, REPO_ID, REPO, list(CASES).index(case_id) + 1, post=False)
            print(f"{case_id}: {result['status']} risk={result.get('risk_label')} "
                  f"issues={result.get('issues_found')} review_id={result.get('review_id')}")
    finally:
        db.close()
    print("Mode:", "LLM + retrieval" if llm else "rules only (GEMINI_API_KEY not set)")


if __name__ == "__main__":
    main(sys.argv[1:])
