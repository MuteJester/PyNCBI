"""Verify that PyPI serves exactly the distribution bytes validated by this release run."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def check_published_files(dist: Path, metadata: dict[str, Any], version: str) -> None:
    if metadata.get("info", {}).get("version") != version:
        raise ValueError(f"PyPI metadata does not identify release {version}")
    local = sorted([*dist.glob("*.whl"), *dist.glob("*.tar.gz")])
    if not any(path.suffix == ".whl" for path in local) or not any(
        path.name.endswith(".tar.gz") for path in local
    ):
        raise ValueError("Expected both a wheel and a source archive")
    published = {entry["filename"]: entry["digests"]["sha256"] for entry in metadata["urls"]}
    for path in local:
        actual = published.get(path.name)
        if actual is None:
            raise ValueError(f"PyPI has not listed {path.name}")
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"PyPI SHA256 differs from the tested artifact: {path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path)
    parser.add_argument("tag")
    parser.add_argument("--attempts", type=int, default=12)
    args = parser.parse_args()
    version = args.tag.removeprefix("v")
    request = Request(
        f"https://pypi.org/pypi/PyNCBI/{version}/json",
        headers={"User-Agent": "PyNCBI-release-verifier"},
    )
    for attempt in range(max(1, args.attempts)):
        try:
            with urlopen(request, timeout=20) as response:
                metadata = json.load(response)
            check_published_files(args.dist, metadata, version)
            print(f"PyPI {version}: every tested distribution matches its published SHA256")
            return
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            if "SHA256 differs" in str(exc) or attempt + 1 >= max(1, args.attempts):
                raise SystemExit(f"Published release verification failed: {exc}") from exc
            time.sleep(5)


if __name__ == "__main__":
    main()
