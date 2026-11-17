"""Chunker tests: pure parsing, no database / Redis / network."""
import ast
import textwrap
from pathlib import Path

from app.core import chunker
from app.core.chunker import chunk_repository, chunk_source

SAMPLE = b'''import os

def top_level(a, b):
    """doc"""
    return a + b

@decorator
async def fetch(x):
    def inner():
        return 1
    return inner()

class Greeter:
    """Greets."""

    def hello(self, name):
        return f"hi {name}"

    @staticmethod
    def util():
        return 1

    class Nested:
        def deep(self):
            pass

if True:
    def conditional():
        pass
'''


def test_chunks_names_types_and_order():
    got = [(c.name, c.qualified_name, c.type) for c in chunk_source(SAMPLE, "sample.py")]
    assert got == [
        ("top_level", "top_level", "function"),
        ("fetch", "fetch", "function"),
        ("Greeter", "Greeter", "class"),
        ("hello", "Greeter.hello", "method"),
        ("util", "Greeter.util", "method"),
        ("Nested", "Greeter.Nested", "class"),
        ("deep", "Greeter.Nested.deep", "method"),
        ("conditional", "conditional", "function"),
    ]


def test_nested_function_is_not_a_separate_chunk():
    chunks = chunk_source(SAMPLE, "sample.py")
    assert "inner" not in {c.name for c in chunks}
    fetch = next(c for c in chunks if c.name == "fetch")
    assert "def inner" in fetch.text  # it stays inside its parent


def test_decorators_are_included_and_lines_are_1_based():
    chunks = {c.qualified_name: c for c in chunk_source(SAMPLE, "sample.py")}
    assert chunks["fetch"].text.startswith("@decorator\nasync def fetch")
    assert chunks["fetch"].start_line == 7
    assert chunks["Greeter.util"].text.startswith("@staticmethod\ndef util")
    assert chunks["top_level"].start_line == 3


def test_chunk_text_matches_the_source_lines_exactly():
    lines = SAMPLE.decode().splitlines()
    for c in chunk_source(SAMPLE, "sample.py"):
        original = "\n".join(lines[c.start_line - 1:c.end_line])
        assert c.text == textwrap.dedent(original), c.qualified_name


def test_every_chunk_is_valid_standalone_python():
    for c in chunk_source(SAMPLE, "sample.py"):
        ast.parse(c.text)  # raises if a chunk was cut mid-function


def test_syntax_error_does_not_crash_and_keeps_good_code():
    broken = b"def good():\n    return 1\n\ndef broken(:\n    pass\n"
    assert "good" in {c.name for c in chunk_source(broken, "broken.py")}


def test_oversized_class_skipped_and_oversized_function_truncated(monkeypatch):
    monkeypatch.setattr(chunker, "MAX_CHUNK_CHARS", 60)
    src = (b"class Big:\n    def a(self):\n        return 1\n\n    def b(self):\n        return 2\n\n"
           b"def long_fn():\n    return '" + b"x" * 100 + b"'\n")
    chunks = chunk_source(src, "big.py")
    assert [c.name for c in chunks] == ["a", "b", "long_fn"]  # class skipped, methods kept
    assert len(chunks[-1].text) == 60


def test_chunk_repository_ignores_tests_vendored_and_generated_dirs(tmp_path):
    files = {
        "pkg/a.py": "def a():\n    return 1\n",
        "pkg/empty.py": "",
        "pkg/readme.txt": "def not_python(): pass",
        "pkg/test_b.py": "def b(): pass\n",
        "pkg/c_test.py": "def c(): pass\n",
        "pkg/conftest.py": "def d(): pass\n",
        "pkg/__pycache__/z.py": "def z(): pass\n",
        "tests/test_x.py": "def x(): pass\n",
        "venv/lib/site.py": "def v(): pass\n",
        "node_modules/n.py": "def n(): pass\n",
        "migrations/0001.py": "def m(): pass\n",
    }
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    chunks = chunk_repository(tmp_path)
    assert {c.file for c in chunks} == {"pkg/a.py"}  # relative POSIX path


def test_chunking_our_own_codebase_yields_only_valid_python():
    app_dir = Path(__file__).resolve().parents[1] / "app"
    chunks = chunk_repository(app_dir)
    assert chunks
    for c in chunks:
        ast.parse(c.text)