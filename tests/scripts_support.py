"""Helper for tests that need to import a module from the repo's `scripts/` folder."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]


def import_script(name: str) -> ModuleType:
    """Import `scripts/<name>.py` as a module, caching it in `sys.modules`."""
    cached = sys.modules.get(f"_pyncbi_scripts.{name}")
    if cached is not None:
        return cached
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_pyncbi_scripts.{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
