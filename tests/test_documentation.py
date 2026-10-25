"""The documentation must describe the code that actually exists.

Every `pyncbi ...` command and every `PyNCBI.<name>(...)` call printed in the README, the
agent skill, `llms.txt` and the narrative docs is checked against the real argument parser
and the real function signatures. A rename that forgets the docs fails here.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import io
import re
import shlex
from pathlib import Path

import pytest

import PyNCBI
from PyNCBI.cli.parser import build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]

DOC_FILES = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "llms.txt",
    REPO_ROOT / "skills" / "pyncbi" / "SKILL.md",
    REPO_ROOT / "src" / "PyNCBI" / "resources" / "SKILL.md",
    REPO_ROOT / "docs" / "source" / "quickstart.rst",
    REPO_ROOT / "docs" / "source" / "agents.rst",
    REPO_ROOT / "docs" / "source" / "index.rst",
    REPO_ROOT / "docs" / "source" / "workflows.rst",
    REPO_ROOT / "docs" / "source" / "configuration.rst",
    REPO_ROOT / "docs" / "source" / "migration.rst",
    *sorted((REPO_ROOT / "src" / "PyNCBI" / "resources" / "references").glob("*.md")),
]


def _python_blocks(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".rst":
        out: list[str] = []
        current: list[str] = []
        inside = False
        for line in text.splitlines():
            if re.match(r"\s*\.\. code-block:: python", line):
                if current:
                    out.append("\n".join(current))
                inside, current = True, []
                continue
            if inside:
                if line.strip() and not line.startswith((" ", "\t")):
                    inside = False
                    out.append("\n".join(current))
                    current = []
                    continue
                current.append(line[3:] if line.startswith("   ") else line)
        if current:
            out.append("\n".join(current))
        return out
    return re.findall(r"```python\n(.*?)```", text, re.S)


def _cli_commands(path: Path) -> list[str]:
    """Documented `pyncbi ...` invocations, with line continuations joined."""
    commands: list[str] = []
    pending = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if pending:
            pending += " " + line.rstrip("\\").strip()
            if not line.endswith("\\"):
                commands.append(pending)
                pending = ""
            continue
        if not (line == "pyncbi" or line.startswith("pyncbi ")):
            continue
        if line.endswith("\\"):
            pending = line.rstrip("\\").strip()
        else:
            commands.append(line)
    # One documented line may chain two commands with `;`
    expanded: list[str] = []
    for command in commands:
        for part in command.split(";"):
            part = part.strip()
            if part.startswith("pyncbi"):
                expanded.append(part)
    return expanded


DOCS_WITH_CLI = [p for p in DOC_FILES if p.exists()]


@pytest.mark.parametrize("doc", DOCS_WITH_CLI, ids=lambda p: p.name)
def test_every_documented_cli_command_is_valid(doc: Path) -> None:
    failures: list[str] = []
    for command in _cli_commands(doc):
        try:
            # `comments=True` drops the trailing `# explanation` the docs put after commands,
            # while still respecting quotes.
            tokens = shlex.split(command, comments=True)[1:]
        except ValueError as exc:  # pragma: no cover - malformed quoting in docs
            failures.append(f"{command!r} cannot be tokenised: {exc}")
            continue
        if not tokens:
            continue
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                build_parser().parse_args(tokens)
        except SystemExit as exc:
            if exc.code not in (0, None):
                message = stderr.getvalue().strip().splitlines()
                failures.append(f"{command!r} -> {message[-1] if message else 'rejected'}")
    assert failures == [], f"{doc.name} documents commands the CLI rejects: {failures}"


@pytest.mark.parametrize("doc", DOCS_WITH_CLI, ids=lambda p: p.name)
def test_every_documented_call_matches_the_real_signature(doc: Path) -> None:
    failures: list[str] = []
    for index, block in enumerate(_python_blocks(doc)):
        try:
            tree = ast.parse(block)
        except SyntaxError as exc:
            failures.append(f"block {index} is not valid Python: {exc}")
            continue
        imported: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "PyNCBI":
                for alias in node.names:
                    imported[alias.asname or alias.name] = alias.name
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _called_public_name(node, imported)
            if name is None:
                continue
            target = getattr(PyNCBI, name, None)
            if target is None:
                failures.append(f"block {index} calls PyNCBI.{name}, which does not exist")
                continue
            failures.extend(
                f"block {index}: {problem}" for problem in _signature_problems(name, target, node)
            )
    assert failures == [], f"{doc.name} documents calls that do not match the code: {failures}"


def _called_public_name(node: ast.Call, imported: dict[str, str]) -> str | None:
    func = node.func
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "PyNCBI"
    ):
        return func.attr
    if isinstance(func, ast.Name) and func.id in imported:
        return imported[func.id]
    return None


def _signature_problems(name: str, target: object, node: ast.Call) -> list[str]:
    try:
        signature = inspect.signature(target)  # type: ignore[arg-type]
    except (TypeError, ValueError):  # pragma: no cover - builtins without signatures
        return []
    parameters = signature.parameters
    takes_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
    takes_varargs = any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in parameters.values())
    problems: list[str] = []
    for keyword in node.keywords:
        if keyword.arg is None:
            continue
        if keyword.arg not in parameters and not takes_kwargs:
            accepted = [
                n
                for n, p in parameters.items()
                if p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            ]
            problems.append(f"{name}() has no parameter {keyword.arg!r}; it accepts {accepted}")
    given = len([a for a in node.args if not isinstance(a, ast.Starred)])
    slots = [
        n
        for n, p in parameters.items()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if given > len(slots) and not takes_varargs:
        problems.append(f"{name}() was given {given} positional arguments but takes {len(slots)}")
    return problems


def test_the_signature_checker_catches_a_wrong_keyword() -> None:
    """The checker must have teeth: a bad keyword is detected, a good one is not."""
    bad = ast.parse("PyNCBI.inspect_accession('GSE1', no_such_argument=True)").body[0]
    assert isinstance(bad, ast.Expr) and isinstance(bad.value, ast.Call)
    assert _signature_problems("inspect_accession", PyNCBI.inspect_accession, bad.value)

    good = ast.parse("PyNCBI.inspect_accession('GSE1', check_file_sizes=False)").body[0]
    assert isinstance(good, ast.Expr) and isinstance(good.value, ast.Call)
    assert _signature_problems("inspect_accession", PyNCBI.inspect_accession, good.value) == []


def test_documented_envelope_examples_use_the_runtime_schema():
    from PyNCBI.core.formatter import ENVELOPE_SCHEMA_VERSION

    for path in DOC_FILES:
        for version in re.findall(
            r'"schema_version":\s*"([^"\n]+)"[^\n]*"pyncbi_version"',
            path.read_text(encoding="utf-8"),
        ):
            assert version == ENVELOPE_SCHEMA_VERSION, str(path)
