"""Chunker tests: pure parsing, no database / Redis / network."""
import ast
import textwrap
from pathlib import Path

import pytest

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


def _assert_parts_cover_unit_exactly(parts, source: bytes):
    """Every line of the unit is in exactly one part and no text is lost."""
    lines = source.decode().split("\n")
    assert [p.part for p in parts] == list(range(1, len(parts) + 1))
    assert all(p.part_count == len(parts) for p in parts)
    covered = []
    for p in parts:
        original = "\n".join(lines[p.start_line - 1:p.end_line])
        if p.start_line == p.end_line and p.text not in textwrap.dedent(original):
            raise AssertionError(f"part {p.part} text not found on line {p.start_line}")
        if p.start_line != p.end_line:
            assert p.text == textwrap.dedent(original)
        covered.extend(range(p.start_line, p.end_line + 1))
    return covered


def test_oversized_function_is_split_on_statement_boundaries_without_losing_code(monkeypatch):
    monkeypatch.setattr(chunker, "MAX_EMBED_BYTES", 120)
    monkeypatch.setattr(chunker, "MAX_PART_BYTES", 80)
    body = "".join(f"    value_{i} = compute_{i}(value_{i - 1})\n" for i in range(1, 12))
    src = ("@decorated\ndef long_fn(value_0):\n" + body + "    return value_11\n").encode()
    parts = chunk_source(src, "big.py")
    assert len(parts) > 1 and {p.qualified_name for p in parts} == {"long_fn"}
    covered = _assert_parts_cover_unit_exactly(parts, src)
    assert covered == list(range(1, 15))           # decorator .. return, each line once
    assert parts[0].text.startswith("@decorated\ndef long_fn")
    assert all(len(p.text.encode()) <= 80 for p in parts)
    assert all(not p.text.startswith(" ") for p in parts)  # dedented, never cut mid-line


def test_oversized_class_splits_only_its_own_lines_and_keeps_methods(monkeypatch):
    monkeypatch.setattr(chunker, "MAX_EMBED_BYTES", 120)
    monkeypatch.setattr(chunker, "MAX_PART_BYTES", 100)
    src = (b'class Big:\n    """A long class docstring that takes some room."""\n'
           b"    LIMIT = 10\n\n    def a(self):\n        return 1\n\n"
           b"    TAIL = 'after methods'\n\n    def b(self):\n        return 2\n")
    chunks = chunk_source(src, "big.py")
    class_parts = [c for c in chunks if c.type == "class"]
    assert {c.qualified_name for c in chunks if c.type == "method"} == {"Big.a", "Big.b"}
    text = "\n".join(p.text for p in class_parts)
    assert "LIMIT = 10" in text and "TAIL = 'after methods'" in text
    assert "return 1" not in text and "return 2" not in text   # methods are not duplicated
    assert all(c.is_split for c in class_parts)


def test_large_class_made_only_of_methods_yields_a_marked_header_part(monkeypatch):
    monkeypatch.setattr(chunker, "MAX_EMBED_BYTES", 80)
    src = (b"class OnlyMethods:\n    def a(self):\n        return 'aaaaaaaaaaaa'\n\n"
           b"    def b(self):\n        return 'bbbbbbbbbbbbbbbb'\n")
    [header] = [c for c in chunk_source(src, "m.py") if c.type == "class"]
    assert header.text == "class OnlyMethods:" and header.is_split and header.to_dict()["split"]


def test_single_overlong_line_is_cut_into_byte_windows_losing_nothing(monkeypatch):
    monkeypatch.setattr(chunker, "MAX_EMBED_BYTES", 100)
    monkeypatch.setattr(chunker, "MAX_PART_BYTES", 50)
    literal = "é" * 120   # 2-byte characters: cuts must respect character boundaries
    src = f"def blob():\n    return '{literal}'\n".encode()
    parts = chunk_source(src, "blob.py")
    assert len(parts) > 2
    assert all(len(p.text.encode()) <= 50 for p in parts)
    rebuilt = "".join(p.text for p in parts if p.start_line == 2)
    assert rebuilt == f"    return '{literal}'"


def test_split_parts_payload_and_embed_text(monkeypatch):
    monkeypatch.setattr(chunker, "MAX_EMBED_BYTES", 60)
    monkeypatch.setattr(chunker, "MAX_PART_BYTES", 40)
    src = b"def f():\n    a = 1\n    b = 2\n    c = 3\n    d = 4\n    return a + b + c + d\n"
    parts = chunk_source(src, "m.py")
    assert len(parts) > 1
    payload = parts[1].to_dict()
    assert payload["part"] == 2 and payload["part_count"] == len(parts)
    assert parts[1].embed_text.startswith(f"# m.py :: function f (part 2/{len(parts)})\n")
    whole = chunk_source(b"def g():\n    return 1\n", "m.py")[0]
    assert "part" not in whole.to_dict() and whole.embed_text == whole.text


def test_default_budget_never_produces_an_oversized_embedding_input():
    src = ("def huge():\n" + "".join(f"    x{i} = {i}\n" for i in range(800))).encode()
    parts = chunk_source(src, "huge.py")
    assert len(parts) > 1
    assert all(len(p.embed_text.encode()) <= chunker.MAX_EMBED_BYTES for p in parts)


@pytest.mark.parametrize("name", [
    "embedder_backup.py", "embedder_old.py", "embedder copy.py", "embedder (1).py",
    "embedder.bak.py", "embedder-orig.py", "backup_embedder.py", "embedder_backup2.py",
])
def test_backup_files_are_never_indexed(name):
    assert chunker.is_backup_file(name)
    assert chunker.is_ignored_path(f"app/core/{name}")


@pytest.mark.parametrize("path", [
    "app/core/embedder.py", "app/copyright.py", "pkg/bold.py", "pkg/older_api.py",
])
def test_regular_modules_are_indexed(path):
    assert not chunker.is_ignored_path(path)


@pytest.mark.parametrize("path", [
    "alembic/versions/6c55_create.py", "pkg/vendor/x.py", "third_party/lib.py",
    "backups/app.py", "pkg/api_pb2.py", "tests/test_x.py",
])
def test_generated_vendored_migration_and_backup_paths_are_ignored(path):
    assert chunker.is_ignored_path(path)


def test_files_marked_generated_are_skipped(tmp_path):
    (tmp_path / "gen.py").write_text("# Code generated by protoc. DO NOT EDIT.\ndef g(): pass\n")
    (tmp_path / "real.py").write_text("def r():\n    return 1\n")
    assert {c.file for c in chunk_repository(tmp_path)} == {"real.py"}


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
        "pkg/a_backup.py": "def a(): pass\n",
        "backup/a.py": "def a(): pass\n",
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
        if not c.is_split:          # whole units must be valid standalone Python
            ast.parse(c.text)
        assert len(c.embed_text.encode()) <= chunker.MAX_EMBED_BYTES