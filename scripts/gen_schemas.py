"""Regenerate the published JSON Schema files from the dataclasses (`python scripts/gen_schemas.py`)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PyNCBI.metadata.schema import json_schema  # noqa: E402


def main() -> None:
    out_dir = ROOT / "src" / "PyNCBI" / "resources" / "schemas"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("study", "sample"):
        dest = out_dir / f"{name}.schema.json"
        dest.write_text(
            json.dumps(json_schema(name), indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        print(f"wrote {dest}")


if __name__ == "__main__":
    main()
