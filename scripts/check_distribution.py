"""Check release archives and optionally smoke-test the wheel in an isolated environment."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tarfile
import tempfile
import venv
import zipfile
from email.parser import Parser
from pathlib import Path


def check_archives(dist: Path) -> Path:
    wheels = list(dist.glob("*.whl"))
    archives = list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(archives) != 1:
        raise SystemExit("Use a clean output directory with exactly one wheel and one sdist")
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        for name in (
            "PyNCBI/py.typed",
            "PyNCBI/session.pyi",
            "PyNCBI/resources/SKILL.md",
            "PyNCBI/resources/schemas/study.schema.json",
        ):
            if name not in names:
                raise SystemExit(f"Wheel is missing {name}")
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
        runtime = [
            requirement
            for requirement in metadata.get_all("Requires-Dist", [])
            if "extra ==" not in requirement
        ]
        if runtime:
            raise SystemExit(
                f"The runtime must be standard-library-only; wheel declares: {runtime}"
            )
        if any(name.endswith(".pyc") or "__pycache__/" in name for name in names):
            raise SystemExit("Wheel contains bytecode")
    with tarfile.open(archives[0]) as archive:
        names = {"/".join(name.split("/")[1:]) for name in archive.getnames()}
        for name in (
            "tests/conftest.py",
            "tests/scripts_support.py",
            "tests/fixtures/api_surface.json",
            "tests/fixtures/pmc_sample.xml",
            "scripts/gen_api_surface.py",
            "scripts/gen_schemas.py",
            "scripts/gen_session_stubs.py",
            "scripts/sync_agent_skill.py",
            "docs/scripts/gen_cli_reference.py",
            "docs/source/migration.rst",
            "docs/source/conf.py",
            "skills/pyncbi/SKILL.md",
            "assets/pyncbi-logo.svg",
        ):
            if name not in names:
                raise SystemExit(f"Source archive is missing {name}")
    return wheels[0].resolve()


def smoke(wheel: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="pyncbi-wheel-") as folder:
        root = Path(folder)
        venv.EnvBuilder(with_pip=True).create(root / "env")
        python = root / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYNCBI_CACHE_DIR"] = str(root / "cache")
        env["PYNCBI_AGENT_MODE"] = "1"
        subprocess.run(
            [str(python), "-m", "pip", "install", "--no-deps", "--no-index", str(wheel)],
            check=True,
            cwd=root,
            env=env,
        )

        def run(*args, input=None):
            return subprocess.run(
                [str(python), *args],
                input=input,
                text=True,
                capture_output=True,
                check=True,
                cwd=root,
                env=env,
            ).stdout

        run("-m", "PyNCBI.cli", "--version")
        manifest = json.loads(run("-m", "PyNCBI.cli", "schema", "mcp"))
        if not manifest["tools"]:
            raise SystemExit("Installed wheel has no MCP tools")
        run("-m", "PyNCBI.cli", "schema", "study")
        if not json.loads(run("-m", "PyNCBI.cli", "status", "--json"))["ok"]:
            raise SystemExit("Installed wheel failed offline status")
        messages = (
            "\n".join(
                json.dumps(m)
                for m in [
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18"},
                    },
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                ]
            )
            + "\n"
        )
        replies = [
            json.loads(line) for line in run("-m", "PyNCBI.cli", "mcp", input=messages).splitlines()
        ]
        if len(replies) != 2 or len(replies[1]["result"]["tools"]) != len(manifest["tools"]):
            raise SystemExit("Installed MCP server did not return its tool manifest")
        print(
            "Dependency-free installed-wheel CLI, schemas, offline status and MCP smoke checks passed"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    wheel = check_archives(args.dist)
    if args.smoke:
        smoke(wheel)
    print("Distribution contents passed")


if __name__ == "__main__":
    main()
