"""
Prepare the `codelens-demo` repository: one branch per benchmark case.

Usage (from the codelens-ci folder, venv active):

  python scripts/create_demo_prs.py ..\\codelens-demo                 # create branches locally
  python scripts/create_demo_prs.py ..\\codelens-demo --push          # ...and push them
  python scripts/create_demo_prs.py ..\\codelens-demo --push --open   # ...and open PRs (needs GitHub CLI `gh`)
  python scripts/create_demo_prs.py ..\\codelens-demo --cases r02 r03 c01

The target must be a git clone of YOUR demo repository (public, sample code only).
`main` gets the clean sample app (benchmarks/sample_repo); each branch then applies
exactly one seeded change from benchmarks/review_benchmark.json, so every pull
request has one known defect (or none, for the clean cases).
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.evaluation.run import SAMPLE_REPO, _apply_case  # noqa: E402

CASES = json.loads((ROOT / "benchmarks" / "review_benchmark.json").read_text())["cases"]
LABEL = {"r01": "weak session token", "r02": "SQL injection", "r03": "IDOR (missing ownership check)",
         "r04": "discount logic bug", "r05": "path traversal", "r06": "command injection",
         "r07": "hardcoded credential", "r08": "tax logic bug",
         "c01": "clean change", "c02": "clean change", "c03": "clean change"}


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("demo_repo", type=Path, help="path to your local clone of codelens-demo")
    parser.add_argument("--cases", nargs="*", help="case ids (default: all)")
    parser.add_argument("--push", action="store_true", help="push main and the branches to origin")
    parser.add_argument("--open", action="store_true", help="open pull requests with the GitHub CLI (gh)")
    args = parser.parse_args()

    repo = args.demo_repo.resolve()
    if not (repo / ".git").is_dir():
        raise SystemExit(f"{repo} is not a git clone. Clone your codelens-demo repository first.")
    if git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("The demo repository has uncommitted changes; commit or discard them first.")

    if not git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False):
        # Freshly created empty repository: make the first commit on `main`.
        git(repo, "checkout", "-B", "main")
        if not (repo / "README.md").exists():
            (repo / "README.md").write_text("# codelens-demo\n\nDemo repository for CodeLens CI.\n", encoding="utf-8")
        git(repo, "add", "README.md")
        git(repo, "commit", "-m", "Initial commit")
        print("[main] created the first commit (repository was empty)")
    main_branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if not (repo / "shopapp").is_dir():
        shutil.copytree(SAMPLE_REPO / "shopapp", repo / "shopapp")
        git(repo, "add", "shopapp")
        git(repo, "commit", "-m", "Add shopapp sample application")
        print(f"[{main_branch}] added shopapp")
    if args.push:
        git(repo, "push", "origin", main_branch)

    selected = [c for c in CASES if not args.cases or c["id"] in args.cases]
    for case in selected:
        branch = f"demo/{case['id']}-{slug(case['title'])}"
        _, new_content = _apply_case(case)
        git(repo, "checkout", main_branch)
        git(repo, "branch", "-D", branch, check=False)
        git(repo, "checkout", "-b", branch)
        (repo / case["file"]).write_text(new_content, encoding="utf-8", newline="\n")
        git(repo, "add", case["file"])
        git(repo, "commit", "-m", case["title"])
        print(f"[{branch}] {case['title']}  ({LABEL.get(case['id'], '')})")
        if args.push:
            git(repo, "push", "--force-with-lease", "-u", "origin", branch)
        if args.open:
            if not shutil.which("gh"):
                raise SystemExit("--open needs the GitHub CLI (https://cli.github.com) or open the PRs on github.com")
            subprocess.run(["gh", "pr", "create", "--base", main_branch, "--head", branch, "--title", case["title"],
                            "--body", f"CodeLens CI demo: {LABEL.get(case['id'], 'seeded change')}."], cwd=repo, check=False)
    git(repo, "checkout", main_branch)

    print(f"\nCreated {len(selected)} branch(es).")
    if not args.push:
        print("Push them with:  python scripts/create_demo_prs.py <demo_repo> --push")
    elif not args.open:
        print("Open each pull request on GitHub: your repo -> 'Compare & pull request' for every demo/* branch.")


if __name__ == "__main__":
    main()
