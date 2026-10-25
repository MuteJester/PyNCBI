"""Generate the installable skill from the canonical packaged skill."""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = ROOT / "src" / "PyNCBI" / "resources" / "SKILL.md"
    target = ROOT / "skills" / "pyncbi" / "SKILL.md"
    content = source.read_text(encoding="utf-8")
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            parser.exit(1, "Run python scripts/sync_agent_skill.py\n")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
