"""Architecture invariants: module size, layering, and a pinned public API surface.

These tests exist so that reorganising the package can never silently change what
callers import or invert the dependency direction between layers.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts_support import import_script  # noqa: F401  (ensures src/ is importable)

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "PyNCBI"
SNAPSHOT_PATH = REPO_ROOT / "tests" / "fixtures" / "api_surface.json"

# A module beyond this many lines stops being reviewable in one sitting and starts
# hiding its seams. Packages split by responsibility instead.
MAX_MODULE_LINES = 1200

# Layer order: a module may only import from layers at or below its own rank.
# Deferred (function-level) and TYPE_CHECKING imports are exempt; only module-level
# imports are ranked, because those are what fix the real dependency direction.
LAYER_RANK = {
    "core": 0,
    "io": 1,
    "parsing": 2,
    "domains": 3,
    "metadata": 4,
    "geo": 5,
    "__root__": 6,
    "mcp": 7,
    "cli": 8,
}


def _python_sources() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def _layer_of(path: Path) -> str:
    parts = path.relative_to(SRC_ROOT).parts
    return parts[0] if len(parts) > 1 else "__root__"


def _module_level_pyncbi_imports(path: Path) -> list[str]:
    """PyNCBI modules imported at module level (not inside functions or TYPE_CHECKING)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("PyNCBI"):
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found.extend(a.name for a in node.names if a.name.startswith("PyNCBI"))
    return found


def test_no_source_module_exceeds_the_size_budget() -> None:
    oversized = {
        str(path.relative_to(REPO_ROOT)): len(path.read_text(encoding="utf-8").splitlines())
        for path in _python_sources()
        if len(path.read_text(encoding="utf-8").splitlines()) > MAX_MODULE_LINES
    }
    assert oversized == {}, (
        f"modules over the {MAX_MODULE_LINES}-line budget: {oversized}; "
        "split them into a package of focused modules"
    )


def test_module_level_imports_never_invert_the_layer_order() -> None:
    violations: list[str] = []
    for path in _python_sources():
        layer = _layer_of(path)
        rank = LAYER_RANK[layer]
        for imported in _module_level_pyncbi_imports(path):
            parts = imported.split(".")
            target = parts[1] if len(parts) > 1 else "__root__"
            if target == layer or target not in LAYER_RANK:
                continue
            if LAYER_RANK[target] > rank:
                violations.append(f"{path.relative_to(REPO_ROOT)} imports {imported}")
    assert violations == [], (
        "these module-level imports point upwards through the layer stack "
        f"({' < '.join(sorted(LAYER_RANK, key=lambda k: LAYER_RANK[k]))}): {violations}"
    )


def test_public_api_surface_matches_the_pinned_snapshot() -> None:
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    gen = import_script("gen_api_surface")
    actual = json.loads(json.dumps(gen.build_surface(), sort_keys=True))

    for module_name in sorted(expected):
        want = expected[module_name]
        got = actual.get(module_name)
        assert got is not None, f"{module_name} is no longer importable"
        missing = sorted(set(want["__all__"]) - set(got["__all__"]))
        assert missing == [], f"{module_name} no longer exports: {missing}"
        for name, want_desc in want["members"].items():
            got_desc = got["members"][name]
            assert got_desc["kind"] == want_desc["kind"], f"{module_name}.{name} changed kind"
            if "params" in want_desc:
                assert got_desc.get("params") == want_desc["params"], (
                    f"{module_name}.{name} signature changed"
                )
            if "members" in want_desc:
                lost = sorted(set(want_desc["members"]) - set(got_desc.get("members", [])))
                assert lost == [], f"{module_name}.{name} lost public members: {lost}"


def test_api_surface_snapshot_is_not_stale() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "gen_api_surface.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize("package", sorted(LAYER_RANK))
def test_every_package_has_an_init(package: str) -> None:
    if package == "__root__":
        assert (SRC_ROOT / "__init__.py").is_file()
        return
    assert (SRC_ROOT / package / "__init__.py").is_file()


# Text files must be written and read as UTF-8 everywhere. Without an explicit encoding,
# Python uses the platform default, which is cp1252 on Windows: GEO metadata is full of
# "TGF-beta" written with a real Greek beta, and those writes raise UnicodeEncodeError.
_TEXT_IO_METHODS = {"write_text", "read_text"}
_BINARY_MODES = {"rb", "wb", "ab", "r+b", "w+b", "a+b", "br", "bw", "ba", "xb", "bx"}


def _text_io_without_encoding(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {k.arg for k in node.keywords}
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in _TEXT_IO_METHODS and "encoding" not in keywords:
            offenders.append(f"{path.name}:{node.lineno} {name}()")
        elif name == "open" and "encoding" not in keywords:
            # Compressed and archive handles do their own decoding.
            owner = (
                func.value.id
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                else ""
            )
            if owner in {"gzip", "tarfile", "zipfile", "bz2", "lzma"}:
                continue
            mode_node: ast.expr | None = node.args[0] if node.args else None
            for keyword in node.keywords:
                if keyword.arg == "mode":
                    mode_node = keyword.value
            # A mode computed at runtime cannot be judged here; those sites are binary
            # downloads, and a text-mode one would still be caught by the Windows CI job.
            if mode_node is not None and not isinstance(mode_node, ast.Constant):
                continue
            mode = str(mode_node.value) if isinstance(mode_node, ast.Constant) else ""
            if ":" in mode:  # tarfile stream mode such as "r:*"
                continue
            if "b" not in mode:
                qualifier = f" (mode={mode!r})" if mode else ""
                offenders.append(f"{path.name}:{node.lineno} open(){qualifier}")
    return offenders


def test_text_files_are_never_written_with_the_platform_default_encoding() -> None:
    offenders: list[str] = []
    for path in _python_sources():
        offenders.extend(_text_io_without_encoding(path))
    assert offenders == [], (
        "these calls use the platform default encoding, which is cp1252 on Windows and "
        f"cannot encode the Greek letters common in GEO metadata: {offenders}"
    )


def test_network_and_worker_creation_use_the_shared_io_components() -> None:
    """HTTP policy and context propagation must not be bypassed by a domain loader."""
    offenders = []
    for path in _python_sources():
        relative = path.relative_to(SRC_ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
                if (
                    node.module == "concurrent.futures"
                    and any(alias.name == "ThreadPoolExecutor" for alias in node.names)
                    and relative != "io/transfer_limits.py"
                ):
                    offenders.append(f"{relative}:{node.lineno} imports an unscoped worker pool")
            else:
                continue
            if relative != "io/http_client.py" and any(
                module == name or module.startswith(name + ".")
                for module in modules
                for name in ("requests", "urllib3", "urllib.request", "http.client")
            ):
                offenders.append(f"{relative}:{node.lineno} bypasses NCBIClient")
    assert offenders == [], offenders


def test_measurement_csv_reading_uses_the_shared_parser() -> None:
    # Annotation text and Entrez run metadata are different contracts. Every
    # measurement table must share the identifier/precision/NA policy instead.
    owners = {"parsing/tabular.py", "domains/annotation.py", "domains/entrez.py"}
    offenders = []
    for path in _python_sources():
        relative = path.relative_to(SRC_ROOT).as_posix()
        if relative in owners:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        pandas_names = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name == "pandas"
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "read_csv"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in pandas_names
            ):
                offenders.append(f"{relative}:{node.lineno} reads CSV outside the shared parser")
    assert offenders == [], offenders
