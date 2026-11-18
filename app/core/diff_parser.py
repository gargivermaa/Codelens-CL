"""
Pull request diff parsing.

Input: the items returned by GitHub's "list pull request files" API
(filename, status, previous_filename, additions, deletions, patch).
Output: ChangedFile objects with exact new-file line numbers for every added
line, plus the set of lines GitHub accepts inline review comments on.

GitHub review comments use (path, line, side="RIGHT"), where `line` is the
line number in the new file and must be inside a diff hunk. `commentable_lines`
is exactly that set (added + context lines of every hunk).

Treat patches as untrusted text: they are parsed, never executed.
"""
import re
from dataclasses import dataclass, field

from app.core.chunker import is_backup_file, is_ignored_path

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")

# Languages the review pipeline understands (Tree-sitter chunking + rules).
REVIEWABLE_EXTENSIONS = {".py": "python"}

_LOCK_OR_GENERATED = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|Pipfile\.lock|uv\.lock|"
    r"Cargo\.lock|go\.sum)$|\.min\.(js|css)$|_pb2(_grpc)?\.py$"
)


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: list[str] = field(default_factory=list)   # raw patch lines incl. +/-/space prefix


@dataclass
class ChangedFile:
    path: str
    status: str                        # added | modified | removed | renamed | copied | changed
    previous_path: str | None = None
    additions: int = 0
    deletions: int = 0
    patch: str | None = None
    hunks: list[Hunk] = field(default_factory=list)
    added_lines: dict[int, str] = field(default_factory=dict)     # new line no -> content
    removed_lines: dict[int, str] = field(default_factory=dict)   # old line no -> content
    commentable_lines: set[int] = field(default_factory=set)
    language: str | None = None
    skip_reason: str | None = None
    patch_truncated: bool = False

    @property
    def reviewable(self) -> bool:
        return self.skip_reason is None

    def annotated_patch(self, max_lines: int | None = None) -> str:
        """Patch with new-file line numbers, the format the LLM is given."""
        out: list[str] = []
        for hunk in self.hunks:
            out.append(hunk.header)
            new_no = hunk.new_start
            for raw in hunk.lines:
                tag, text = raw[:1], raw[1:]
                if tag == "+":
                    out.append(f"{new_no:>5} + {text}")
                    new_no += 1
                elif tag == "-":
                    out.append(f"      - {text}")
                elif tag == " ":
                    out.append(f"{new_no:>5}   {text}")
                    new_no += 1
                if max_lines and len(out) >= max_lines:
                    out.append("      ... (diff truncated)")
                    return "\n".join(out)
        return "\n".join(out)


def parse_patch(patch: str) -> list[Hunk]:
    hunks: list[Hunk] = []
    current: Hunk | None = None
    old_left = new_left = 0
    for line in patch.split("\n"):
        match = _HUNK_HEADER.match(line)
        if match:
            old_start, old_count, new_start, new_count, _ = match.groups()
            current = Hunk(int(old_start), int(1 if old_count is None else old_count),
                           int(new_start), int(1 if new_count is None else new_count), line)
            hunks.append(current)
            old_left, new_left = current.old_count, current.new_count
            continue
        if current is None or (old_left <= 0 and new_left <= 0):
            continue  # outside a hunk, or "\ No newline at end of file"
        tag = line[:1]
        if line == "":
            # A blank context line whose single leading space was stripped.
            line, tag = " ", " "
        if tag == "+":
            new_left -= 1
        elif tag == "-":
            old_left -= 1
        elif tag == " ":
            old_left -= 1
            new_left -= 1
        else:
            continue
        current.lines.append(line)
    return hunks


def _apply_hunks(changed: ChangedFile) -> None:
    for hunk in changed.hunks:
        old_no, new_no = hunk.old_start, hunk.new_start
        for raw in hunk.lines:
            tag, text = raw[:1], raw[1:]
            if tag == "+":
                changed.added_lines[new_no] = text
                changed.commentable_lines.add(new_no)
                new_no += 1
            elif tag == "-":
                changed.removed_lines[old_no] = text
                old_no += 1
            else:
                changed.commentable_lines.add(new_no)
                old_no += 1
                new_no += 1


def skip_reason_for(path: str, status: str, patch: str | None, changes: int) -> str | None:
    if status == "removed":
        return "file deleted"
    extension = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    if _LOCK_OR_GENERATED.search(path):
        return "lockfile or generated file"
    if is_backup_file(path.rsplit("/", 1)[-1]):
        return "backup file"
    if extension not in REVIEWABLE_EXTENSIONS:
        return f"unsupported file type ({extension or 'none'})"
    if is_ignored_path(path):
        return "test, vendored, migration or generated path"
    if patch is None:
        # GitHub omits `patch` for binary files and for very large diffs.
        return "binary file or diff too large for the GitHub API" if changes else "no textual changes"
    return None


def parse_pull_request_files(files: list[dict], max_patch_lines: int = 2000,
                             max_files: int = 30) -> list[ChangedFile]:
    """Turn GitHub PR file entries into ChangedFile objects (order preserved)."""
    result: list[ChangedFile] = []
    reviewable = 0
    for item in files:
        path = item["filename"]
        status = item.get("status", "modified")
        patch = item.get("patch")
        changed = ChangedFile(
            path=path, status=status, previous_path=item.get("previous_filename"),
            additions=int(item.get("additions") or 0), deletions=int(item.get("deletions") or 0),
            patch=patch,
        )
        changed.skip_reason = skip_reason_for(path, status, patch, int(item.get("changes") or 0))
        if changed.skip_reason is None and reviewable >= max_files:
            changed.skip_reason = f"over the {max_files}-file review limit"
        if changed.skip_reason is None:
            changed.language = REVIEWABLE_EXTENSIONS[path[path.rfind("."):]]
            lines = patch.split("\n")
            if len(lines) > max_patch_lines:
                # Keep only whole hunks that fit, so line numbers stay exact.
                patch = "\n".join(lines[:max_patch_lines])
                cut = patch.rfind("\n@@ ")
                patch = patch[:cut] if cut > 0 else patch
                changed.patch_truncated = True
            changed.hunks = parse_patch(patch)
            _apply_hunks(changed)
            reviewable += 1
        result.append(changed)
    return result
