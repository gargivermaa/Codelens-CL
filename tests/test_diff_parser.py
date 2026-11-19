"""Diff parsing tests (pure, no network)."""
from app.core.diff_parser import parse_patch, parse_pull_request_files

PATCH = """@@ -1,4 +1,5 @@
 import os
-def old():
+def new():
+    x = 1
     return 1

@@ -10,3 +11,3 @@ def other():
     a = 1
-    b = 2
+    b = 3
     c = 4
\\ No newline at end of file"""


def test_hunks_and_line_numbers():
    hunks = parse_patch(PATCH)
    assert [(h.old_start, h.new_start) for h in hunks] == [(1, 1), (10, 11)]
    [changed] = parse_pull_request_files([{"filename": "app/x.py", "status": "modified", "patch": PATCH,
                                           "additions": 3, "deletions": 2, "changes": 5}])
    assert changed.added_lines == {2: "def new():", 3: "    x = 1", 12: "    b = 3"}
    assert changed.removed_lines == {2: "def old():", 11: "    b = 2"}
    # Inline comments are only valid on lines inside hunks (added + context).
    assert changed.commentable_lines == {1, 2, 3, 4, 5, 11, 12, 13}
    assert changed.reviewable and changed.language == "python"


def test_annotated_patch_shows_new_line_numbers():
    [changed] = parse_pull_request_files([{"filename": "x.py", "status": "modified", "patch": PATCH}])
    text = changed.annotated_patch()
    assert "    2 + def new():" in text and "      - def old():" in text and "   12 +     b = 3" in text


def test_added_file_and_renamed_file():
    files = parse_pull_request_files([
        {"filename": "new.py", "status": "added", "patch": "@@ -0,0 +1,2 @@\n+a = 1\n+b = 2"},
        {"filename": "pkg/b.py", "previous_filename": "pkg/a.py", "status": "renamed",
         "patch": "@@ -1 +1 @@\n-x = 1\n+x = 2"},
        {"filename": "pkg/c.py", "previous_filename": "pkg/d.py", "status": "renamed", "changes": 0},
    ])
    assert files[0].added_lines == {1: "a = 1", 2: "b = 2"}
    assert files[1].previous_path == "pkg/a.py" and files[1].added_lines == {1: "x = 2"}
    assert files[2].skip_reason == "no textual changes"   # pure rename


def test_irrelevant_files_are_skipped_with_reasons():
    files = parse_pull_request_files([
        {"filename": "gone.py", "status": "removed", "patch": "@@ -1 +0,0 @@\n-x"},
        {"filename": "logo.png", "status": "added", "changes": 10},
        {"filename": "README.md", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"},
        {"filename": "poetry.lock", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"},
        {"filename": "tests/test_a.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"},
        {"filename": "app/api_pb2.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"},
        {"filename": "app/big.py", "status": "modified", "changes": 50000},
        {"filename": "app/core/embedder_backup.py", "status": "added", "patch": "@@ -0,0 +1 @@\n+a"},
    ])
    reasons = [f.skip_reason for f in files]
    assert reasons[0] == "file deleted"
    assert reasons[1].startswith("unsupported") and reasons[2].startswith("unsupported")
    assert reasons[3] == "lockfile or generated file" and reasons[5] == "lockfile or generated file"
    assert reasons[4].startswith("test")
    assert reasons[6].startswith("binary file or diff too large")
    assert reasons[7] == "backup file"
    assert not any(f.reviewable for f in files)


def test_large_patch_is_cut_at_a_hunk_boundary_and_file_limit_applies():
    hunk = "@@ -{0},1 +{0},1 @@\n-a\n+b\n"
    patch = "".join(hunk.format(i * 10 + 1) for i in range(100))
    [changed] = parse_pull_request_files([{"filename": "x.py", "status": "modified", "patch": patch}],
                                         max_patch_lines=50)
    assert changed.patch_truncated and len(changed.hunks) == 16
    assert all(line == "b" for line in changed.added_lines.values())

    many = [{"filename": f"m{i}.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"} for i in range(5)]
    parsed = parse_pull_request_files(many, max_files=3)
    assert [f.reviewable for f in parsed] == [True, True, True, False, False]
