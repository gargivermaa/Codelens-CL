"""
Semantic code chunking with Tree-sitter.

Splits Python source into complete syntactic units (functions, methods,
classes) instead of fixed-size character windows, so every chunk that is
later embedded and retrieved is a whole, meaningful piece of code.

Chunking rules
  * Top-level and conditionally-defined functions -> type "function".
  * Functions defined inside a class -> type "method".
  * Classes -> type "class" (full text), AND each method is also its own chunk,
    so retrieval can match either the whole class or one method.
  * Functions nested inside other functions are NOT separate chunks; they stay
    part of their parent function's text.
  * Decorators are part of the chunk (@app.post(...), @login_required, ...).
  * Class chunks larger than MAX_CHUNK_CHARS are skipped (their methods are
    still indexed); oversized functions are truncated to fit the embedding
    model's input limit.

Day 7 scope: parsing only. Nothing here talks to a database, OpenAI or Qdrant.
"""
import argparse
import logging
import os
import textwrap
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

logger = logging.getLogger("codelens.chunker")

PY_LANGUAGE = Language(tspython.language())

# text-embedding-3-small accepts 8191 tokens (~4 chars/token). 24k chars keeps a
# safe margin even for token-dense code.
MAX_CHUNK_CHARS = 24_000

# Larger files are almost always generated/vendored code, not worth indexing.
MAX_FILE_BYTES = 1_000_000

IGNORED_DIRS = frozenset({
    ".git", ".hg", ".svn", "venv", ".venv", "env", "node_modules", "__pycache__",
    "build", "dist", "site-packages", ".tox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "migrations", "tests", "test",
})

_DEFINITIONS = ("function_definition", "class_definition")


@dataclass(frozen=True)
class CodeChunk:
    text: str
    file: str            # path relative to the repo root, POSIX separators
    name: str            # "hello"
    qualified_name: str  # "Greeter.hello"
    type: str            # "function" | "method" | "class"
    start_line: int      # 1-based, inclusive (includes decorators)
    end_line: int        # 1-based, inclusive

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_text(source: bytes, node: Node) -> str:
    """Return the node's source, dedented so a method reads like a top-level def.

    Tree-sitter's node range starts at the `def`/`class`/decorator token, so the
    first line has lost its indentation while later lines keep theirs. We put the
    original indentation back on the first line, then dedent the whole block.
    """
    line_start = source.rfind(b"\n", 0, node.start_byte) + 1
    indent = source[line_start:node.start_byte]
    if indent.strip():  # code precedes the node on its line; nothing to restore
        indent = b""
    raw = (indent + source[node.start_byte:node.end_byte]).decode("utf-8", errors="replace")
    return textwrap.dedent(raw.replace("\r\n", "\n"))


def _collect(node: Node, source: bytes, file_path: str, parent: str | None,
             chunks: list[CodeChunk]) -> None:
    for child in node.children:
        # `@decorator` + def are wrapped in a decorated_definition node; the span
        # we cut from the source must be the wrapper so decorators are included.
        definition = child
        if child.type == "decorated_definition":
            definition = child.child_by_field_name("definition")

        if definition is None or definition.type not in _DEFINITIONS:
            # Not a definition: look inside it (if/try/with blocks, class bodies...).
            _collect(child, source, file_path, parent, chunks)
            continue

        name_node = definition.child_by_field_name("name")
        if name_node is None:  # only possible on badly broken syntax
            continue
        name = source[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
        qualified = f"{parent}.{name}" if parent else name
        is_class = definition.type == "class_definition"
        kind = "class" if is_class else ("method" if parent else "function")

        text: str | None = _extract_text(source, child)
        if len(text) > MAX_CHUNK_CHARS:
            if is_class:
                logger.warning("Skipping oversized class chunk %s in %s (%d chars); its methods are still indexed",
                               qualified, file_path, len(text))
                text = None
            else:
                logger.warning("Truncating oversized %s %s in %s (%d chars)",
                               kind, qualified, file_path, len(text))
                text = text[:MAX_CHUNK_CHARS]

        if text is not None:
            chunks.append(CodeChunk(
                text=text,
                file=file_path,
                name=name,
                qualified_name=qualified,
                type=kind,
                start_line=child.start_point[0] + 1,
                end_line=child.end_point[0] + 1,
            ))

        # Descend into classes to find their methods. Function bodies are NOT
        # descended into: nested functions stay inside the parent chunk.
        if is_class:
            _collect(definition, source, file_path, qualified, chunks)


def chunk_source(source: bytes, file_path: str) -> list[CodeChunk]:
    """Chunk one Python file's bytes. `file_path` is stored as chunk metadata."""
    tree = Parser(PY_LANGUAGE).parse(source)
    if tree.root_node.has_error:
        # Tree-sitter recovers from syntax errors, so we still get every
        # definition it managed to parse.
        logger.warning("Syntax errors in %s; chunking the parseable parts", file_path)
    chunks: list[CodeChunk] = []
    _collect(tree.root_node, source, file_path, None, chunks)
    return chunks


def chunk_file(file_path: Path, repo_root: Path) -> list[CodeChunk]:
    try:
        if file_path.stat().st_size > MAX_FILE_BYTES:
            logger.warning("Skipping %s: larger than %d bytes", file_path, MAX_FILE_BYTES)
            return []
        source = file_path.read_bytes()
    except OSError as exc:
        logger.warning("Skipping unreadable file %s: %s", file_path, exc)
        return []
    return chunk_source(source, file_path.relative_to(repo_root).as_posix())


def _is_indexable(filename: str) -> bool:
    if not filename.endswith(".py"):
        return False
    is_test_file = (
        filename.startswith("test_") or filename.endswith("_test.py") or filename == "conftest.py"
    )
    return not is_test_file


def chunk_repository(repo_path: str | Path) -> list[CodeChunk]:
    """Chunk every indexable Python file under `repo_path`, in a stable order."""
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")

    chunks: list[CodeChunk] = []
    for dirpath, dirnames, filenames in os.walk(root):  # symlinks are not followed
        dirnames[:] = sorted(
            d for d in dirnames if d not in IGNORED_DIRS and not d.endswith(".egg-info")
        )
        for filename in sorted(filenames):
            if _is_indexable(filename):
                chunks.extend(chunk_file(Path(dirpath) / filename, root))
    return chunks


def main() -> None:
    """Dev tool: python -m app.core.chunker <repo_path> [--show N]"""
    parser = argparse.ArgumentParser(description="Chunk a Python repository and print the result.")
    parser.add_argument("repo_path")
    parser.add_argument("--show", type=int, default=10, help="how many chunks to print (default 10)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    chunks = chunk_repository(args.repo_path)

    by_type = Counter(c.type for c in chunks)
    print(f"{len(chunks)} chunks from {len({c.file for c in chunks})} files | "
          + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))

    for c in chunks[: args.show]:
        print(f"\n--- {c.type} {c.qualified_name}  {c.file}:{c.start_line}-{c.end_line}  ({len(c.text)} chars)")
        lines = c.text.splitlines()
        print(textwrap.indent("\n".join(lines[:6]), "    "))
        if len(lines) > 6:
            print(f"    ... ({len(lines) - 6} more lines)")


if __name__ == "__main__":
    main()