"""Packaging and Python-floor guards: the declared floor (3.10) must stay true, and the
distribution must keep shipping the agent skill, JSON schemas, typing marker and entry points."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "PyNCBI"
FLOOR = (3, 10)

VIOLATING_SNIPPETS = {
    "pep695_class": "class Cache[T]:\n    pass\n",
    "pep695_function": "def first[T](items: list[T]) -> T:\n    return items[0]\n",
    "pep695_type_alias": "type Alias = int | str\n",
    "datetime_utc_import": "from datetime import UTC\n",
    "datetime_utc_attr": "import datetime\nx = datetime.datetime.now(datetime.UTC)\n",
}


def _find_floor_violations(tree: ast.AST) -> list[str]:
    """Report constructs that need Python above 3.10."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if getattr(node, "type_params", ()):  # PEP 695 (3.12)
                found.append(f"PEP 695 type parameters on '{node.name}'")
        elif node.__class__.__name__ == "TypeAlias":  # PEP 695 `type X = ...` (3.12)
            found.append("PEP 695 `type` alias statement")
        elif isinstance(node, ast.ImportFrom) and node.module == "datetime":
            for alias in node.names:
                if alias.name == "UTC":  # datetime.UTC (3.11)
                    found.append("`from datetime import UTC`")
        elif isinstance(node, ast.Attribute) and node.attr == "UTC":
            base = node.value
            if isinstance(base, ast.Name) and base.id == "datetime":
                found.append("`datetime.UTC`")
    return found


def test_the_floor_detector_catches_each_construct_it_guards():
    for label, snippet in VIOLATING_SNIPPETS.items():
        try:
            tree = ast.parse(snippet)
        except SyntaxError:
            # Older supported interpreters reject PEP 695 syntax before producing an AST.
            assert label.startswith("pep695_")
        else:
            assert _find_floor_violations(tree), f"detector missed {label}"
    assert (
        _find_floor_violations(
            ast.parse("from datetime import datetime, timezone\nx = datetime.now(timezone.utc)\n")
        )
        == []
    )


def test_no_source_file_needs_python_above_the_declared_floor():
    offenders: dict[str, list[str]] = {}
    files = sorted(SRC.rglob("*.py"))
    assert len(files) > 30, "source tree looks empty; check the test's ROOT"
    for path in files:
        violations = _find_floor_violations(ast.parse(path.read_text(encoding="utf-8")))
        if violations:
            offenders[str(path.relative_to(ROOT))] = violations
    assert offenders == {}, f"Python >{FLOOR[0]}.{FLOOR[1]} constructs found: {offenders}"


def test_declared_python_support_matches_the_floor():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'requires-python\s*=\s*">=3\.10"', text)
    for minor in range(10, 16):
        assert f'"Programming Language :: Python :: 3.{minor}"' in text, minor
    assert re.search(r'target-version\s*=\s*"py310"', text)  # ruff lints for the floor
    assert 'pyncbi = "PyNCBI.cli.main:main"' in text


def test_distribution_ships_skill_schemas_and_typing_marker():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'"\*"\s*=\s*\[[^\]]*py\.typed', text)
    assert "resources/schemas/*.json" in text
    assert (SRC / "py.typed").exists()
    assert (SRC / "resources" / "SKILL.md").exists()
    for name in ("study", "sample"):
        assert (SRC / "resources" / "schemas" / f"{name}.schema.json").exists()


def test_published_json_schemas_are_in_sync_with_the_dataclasses():
    import json

    from PyNCBI.metadata import json_schema

    for name in ("study", "sample"):
        published = json.loads((SRC / "resources" / "schemas" / f"{name}.schema.json").read_text())
        assert published == json_schema(name), f"run scripts/gen_schemas.py ({name} is stale)"


def test_container_image_runs_the_mcp_server_as_a_non_root_user():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM python:3\.\d+", dockerfile, re.M)
    assert "USER pyncbi" in dockerfile
    assert 'ENTRYPOINT ["pyncbi"]' in dockerfile and 'CMD ["mcp"]' in dockerfile
    assert "PYNCBI_AGENT_MODE=1" in dockerfile
    assert (ROOT / ".dockerignore").exists()


@pytest.mark.parametrize("name", ["quality", "test", "docs", "build", "deploy"])
def test_ci_defines_the_release_gates(name):
    workflow = (ROOT / ".github" / "workflows" / "python-publish.yml").read_text(encoding="utf-8")
    assert f"\n  {name}:\n" in workflow
    if name == "test":
        for version in ("3.10", "3.11", "3.12", "3.13", "3.14", "3.15"):
            assert f'"{version}"' in workflow
        assert "windows-latest" in workflow
    if name == "quality":
        assert "vermin --target=3.10" in workflow


def test_the_two_skill_copies_are_byte_identical() -> None:
    """`skills/pyncbi/SKILL.md` is installed for agents; the packaged copy is what
    `pyncbi skill` prints. They describe the same tool, so they must not drift."""
    packaged = (SRC / "resources" / "SKILL.md").read_text(encoding="utf-8")
    installable = (ROOT / "skills" / "pyncbi" / "SKILL.md").read_text(encoding="utf-8")
    assert packaged == installable, (
        "the agent skill has two copies and they differ; copy "
        "src/PyNCBI/resources/SKILL.md over skills/pyncbi/SKILL.md"
    )
