#!/usr/bin/env python3
"""Regenerate the public API surface snapshot used by `tests/test_architecture.py`.

The snapshot pins every name PyNCBI exports, along with call signatures and public
members, so that moving code between modules cannot silently change the contract
callers and agents depend on.

Usage::

    python scripts/gen_api_surface.py            # rewrite tests/fixtures/api_surface.json
    python scripts/gen_api_surface.py --check    # exit 1 if the snapshot is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = REPO_ROOT / "tests" / "fixtures" / "api_surface.json"

if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))


def _own_members(cls: type) -> set[str]:
    """Public names this class defines itself, ignoring anything inherited from outside PyNCBI.

    `dir()` cannot be used here: for an `IntEnum` it also reports `int`'s methods, and those
    change between interpreter releases (`bit_count` arrived in 3.10, `is_integer` in 3.12),
    which would make the snapshot fail on every Python but the one that generated it.
    """
    names: set[str] = set()
    for base in cls.__mro__:
        if not getattr(base, "__module__", "").startswith("PyNCBI"):
            continue
        names.update(n for n in vars(base) if not n.startswith("_"))
        names.update(n for n in getattr(base, "__annotations__", {}) if not n.startswith("_"))
    return names


def _describe(obj: object) -> dict[str, object]:
    import inspect
    from enum import Enum

    out: dict[str, object] = {}
    if inspect.isclass(obj):
        out["kind"] = "class"
        out["members"] = sorted(_own_members(obj))
        if issubclass(obj, Enum):
            # EnumType's factory signature varies between Python releases. The public
            # contract of these existing enums is lookup by value, not Enum creation.
            out["params"] = ["value"]
            return out
        target: object | None = obj
    elif callable(obj):
        out["kind"] = "function"
        target = obj
    else:
        out["kind"] = "value"
        return out
    try:
        sig = inspect.signature(target)  # type: ignore[arg-type]
    except (TypeError, ValueError):  # pragma: no cover - builtins without signatures
        return out
    out["params"] = [
        f"{p.name}{'=' if p.default is not inspect.Parameter.empty else ''}"
        for p in sig.parameters.values()
        if p.name != "self"
    ]
    return out


def build_surface() -> dict[str, object]:
    import importlib

    surface: dict[str, object] = {}
    for module_name in ("PyNCBI", "PyNCBI.domains", "PyNCBI.metadata"):
        module = importlib.import_module(module_name)
        exported = sorted(getattr(module, "__all__", []))
        surface[module_name] = {
            "__all__": exported,
            "members": {name: _describe(getattr(module, name)) for name in exported},
        }
    return surface


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the snapshot is stale")
    args = parser.parse_args()

    payload = json.dumps(build_surface(), indent=2, sort_keys=True) + "\n"
    if args.check:
        current = SNAPSHOT_PATH.read_text() if SNAPSHOT_PATH.exists() else ""
        if current != payload:
            print(
                f"{SNAPSHOT_PATH.relative_to(REPO_ROOT)} is stale; "
                "run python scripts/gen_api_surface.py",
                file=sys.stderr,
            )
            return 1
        print("API surface snapshot is up to date.")
        return 0

    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(payload)
    print(f"Wrote {SNAPSHOT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
