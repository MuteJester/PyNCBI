"""Local-state commands: cache, status, skill."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyNCBI.cli.console import _determine_mode, print  # noqa: A001
from PyNCBI.core.formatter import (
    render_output,
)
from PyNCBI.io.cache import LocalCache
from PyNCBI.io.http_client import get_client

if TYPE_CHECKING:
    import argparse


def _cmd_cache(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    cache: LocalCache[Any] = LocalCache()
    action = args.action.lower()

    if action in ("status", "list"):
        status = cache.status_summary()
        print(
            render_output(
                status,
                mode=mode,
                command="cache.status",
                target=str(cache.cache_dir),
                summary=f"Cache contains {status['item_count']} item(s) ({status['total_size_human']})",
            )
        )
        return 0

    if action == "clear":
        deleted = cache.clear(args.pattern)
        print(
            render_output(
                {"deleted_count": deleted, "pattern": args.pattern},
                mode=mode,
                command="cache.clear",
                target=args.pattern,
                summary=f"Cleared {deleted} cached item(s) matching '{args.pattern}'",
            )
        )
        return 0

    return 1


def _cmd_status(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    client = get_client()
    health = client.check_health(probe_network=not getattr(args, "offline", False))
    cfg = health["config"]
    rate = cfg["effective_eutils_rate_limit_per_sec"]
    key_str = "configured" if cfg["api_key_configured"] else "not set"
    summary = (
        f"PyNCBI {health['version']} — E-Utilities rate limit: {rate} req/s (API key: {key_str})"
    )
    print(
        render_output(
            health,
            mode=mode,
            command="status",
            target="ncbi",
            summary=summary,
        )
    )
    return 0


def _cmd_skill(args: argparse.Namespace) -> int:
    skill_path = Path(__file__).resolve().parent.parent / "resources" / "SKILL.md"
    skill_content = skill_path.read_text(encoding="utf-8")

    if args.action == "show":
        print(skill_content)
        return 0

    files = [skill_path, *sorted((skill_path.parent / "references").glob("*.md"))]
    if args.dir:
        destinations = [Path(args.dir)]
    else:
        targets = ["codex", "antigravity", "claude"] if args.target == "all" else [args.target]
        roots = {
            "codex": Path.home() / ".agents" / "skills" / "pyncbi",
            "claude": Path.home() / ".claude" / "skills" / "pyncbi",
            "antigravity": Path.home() / ".gemini" / "antigravity-cli" / "skills" / "pyncbi",
            "cursor": Path.cwd() / ".cursor" / "skills" / "pyncbi",
        }
        destinations = [roots[target] for target in targets]
    installed_paths = []
    for destination in destinations:
        for source in files:
            target = destination / source.relative_to(skill_path.parent)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        installed_paths.append(str(destination / "SKILL.md"))

    print("Installed PyNCBI Agent Skill to:\n" + "\n".join(f"  • {p}" for p in installed_paths))
    return 0
