"""Generate docs/source/cli.rst from the live argparse definition (`python docs/scripts/gen_cli_reference.py`)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from PyNCBI.cli.main import build_parser  # noqa: E402


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _render(name: str, parser: argparse.ArgumentParser, level: str) -> list[str]:
    lines = [f"``pyncbi {name}``", level * (len(name) + 11), ""]
    if parser.description:
        lines += [parser.description, ""]
    lines += [".. code-block:: text", ""]
    lines += ["   " + ln for ln in parser.format_help().splitlines()]
    lines.append("")
    for sub_name, sub in _subparsers(parser).items():
        lines += _render(f"{name} {sub_name}", sub, "^")
    return lines


def main() -> None:
    parser = build_parser()
    out = [
        "CLI Reference",
        "=============",
        "",
        "Generated from the ``pyncbi`` argparse definition. Every command supports ``--json`` (agent envelope), ``--markdown``, ``-o`` (save), ``-v``/``-q``.",
        "",
    ]
    out += (
        [".. code-block:: text", ""]
        + ["   " + ln for ln in parser.format_help().splitlines()]
        + [""]
    )
    for name, sub in _subparsers(parser).items():
        out += _render(name, sub, "-")
    dest = ROOT / "docs" / "source" / "cli.rst"
    dest.write_text("\n".join(line.rstrip() for line in out).rstrip() + "\n", encoding="utf-8")
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
