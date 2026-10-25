"""Generate the installable skill from the canonical packaged skill."""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = ROOT / "src" / "PyNCBI" / "resources"
    target = ROOT / "skills" / "pyncbi"
    files = [source / "SKILL.md", *sorted((source / "references").glob("*.md"))]
    expected = {path.relative_to(source) for path in files}
    stale = [
        path
        for path in (target / "references").glob("*.md")
        if path.relative_to(target) not in expected
    ]
    if args.check:
        if stale or any(
            not (target / relative).exists()
            or (target / relative).read_bytes() != (source / relative).read_bytes()
            for relative in expected
        ):
            parser.exit(1, "Run python scripts/sync_agent_skill.py\n")
    else:
        for path in stale:
            path.unlink()
        for relative in expected:
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((source / relative).read_bytes())


if __name__ == "__main__":
    main()
