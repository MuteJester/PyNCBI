"""Console plumbing shared by every command: safe printing, argument parser, output flags."""

from __future__ import annotations

import argparse
import difflib
import platform
import re
import sys

from PyNCBI import __version__
from PyNCBI.core.formatter import (
    console_safe_text,
)


def _print(text: str = "") -> None:
    """stdout print that never crashes on narrow console encodings (Windows cp1252)."""
    sys.stdout.write(console_safe_text(str(text)) + "\n")


print = _print  # noqa: A001 - every command renders through the console-safe printer


class _Parser(argparse.ArgumentParser):
    """argparse parser with 'did you mean' suggestions for mistyped subcommands/options."""

    def error(self, message: str) -> None:  # type: ignore[override]
        # Python 3.15 adds its own suggestion; keep the CLI wording consistent.
        message = message.replace(", maybe you meant ", ", did you mean ")
        m = re.search(r"invalid choice:? '([^']+)' \(choose from (.+)\)", message)
        if m:
            bad = m.group(1)
            choices = [c.strip().strip("'\"") for c in m.group(2).split(",")]
            close = difflib.get_close_matches(bad, choices, n=3, cutoff=0.5)
            if close:
                quoted = ", ".join(f"'{c}'" for c in close)
                message = f"{message}\n\nDid you mean {quoted}?"
        super().error(message)


def _version_string() -> str:
    parts = [f"pyncbi {__version__}", f"Python {platform.python_version()}", platform.system()]
    parts.append("stdlib only")
    return " | ".join(parts)


def _determine_mode(args: argparse.Namespace) -> str:
    if getattr(args, "json", False):
        return "json"
    if getattr(args, "markdown", False):
        return "markdown"
    return "text"


def _add_output_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-o",
        "--output",
        help="Save output directly to a file (.csv, .tsv, .json) or directory",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Format terminal output as an agent-friendly JSON envelope",
    )
    parser.add_argument(
        "--markdown",
        "--md",
        dest="markdown",
        action="store_true",
        help="Format terminal output as clean GitHub-Flavored Markdown",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Enable step-by-step verbose progress reporting on stderr (-vv for full HTTP debug trace)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress all progress bars and status messages on stderr",
    )
