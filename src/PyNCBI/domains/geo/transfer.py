"""Byte-level transfer helpers: gzip sanity checks, archive unpacking, and parallel HTTP I/O."""

from __future__ import annotations

import gzip
import shutil
import zlib
from concurrent.futures import as_completed
from pathlib import Path
from typing import TYPE_CHECKING

from PyNCBI.core.exceptions import DataError, DownloadError
from PyNCBI.core.models import (
    NCBIFileInfo,
)
from PyNCBI.core.reporter import get_reporter
from PyNCBI.io.compression import extract_tarfile, gunzip_file
from PyNCBI.io.http_client import (
    infer_file_type,
    looks_like_bot_challenge,
)
from PyNCBI.io.transfer_limits import ContextThreadPoolExecutor as ThreadPoolExecutor
from PyNCBI.io.transfer_limits import bounded_transfer
from PyNCBI.io.urls import download_filename

if TYPE_CHECKING:
    from PyNCBI.core.protocols import HttpClient


def decompress_table_bytes(raw: bytes, *, context: str = "table") -> str:
    """Decode a (possibly gzip-compressed) tabular payload into text, rejecting HTML bodies."""
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError, zlib.error) as exc:
            raise DataError(
                f"Corrupt gzip payload while reading {context}: {exc}",
                hint="Retry the download; if it persists the remote file may be truncated",
            ) from exc
    head = raw[:4096].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")) or b"<html" in head[:512]:
        text_head = head.decode("utf-8", errors="replace")
        if looks_like_bot_challenge(text_head):
            raise DataError(
                f"Received a bot-challenge HTML page instead of {context}",
                hint="This NCBI endpoint is browser-gated on your network; see BotChallengeError",
            )
        raise DataError(
            f"Received an HTML page instead of {context}",
            hint="The requested file probably does not exist for this accession (check `pyncbi files`)",
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _unpack_downloaded(dest_file: Path, target_folder: Path) -> Path:
    """Unpack a downloaded `.tar` or `.gz` file in place and return the resulting path."""
    fname = dest_file.name.lower()
    if fname.endswith(".tar"):
        extract_dir = target_folder / dest_file.name[:-4]
        extract_tarfile(dest_file, extract_dir)
        dest_file.unlink(missing_ok=True)
        return extract_dir
    if fname.endswith(".gz") and not fname.endswith(".tar.gz"):
        return gunzip_file(dest_file, dest_file.with_suffix(""), delete_source=True)
    return dest_file


def _free_bytes(destination: Path) -> int | None:
    """Free bytes on the filesystem that will hold `destination` (None if undeterminable)."""
    probe = destination
    for _ in range(64):
        if probe.exists():
            break
        if probe.parent == probe:
            break
        probe = probe.parent
    try:
        return int(shutil.disk_usage(probe).free)
    except OSError:
        return None


@bounded_transfer
def download_urls_parallel(
    client: HttpClient,
    urls: list[str],
    target_folder: str | Path,
    *,
    max_workers: int = 4,
    unpack: bool = False,
) -> list[Path]:
    """Download many NCBI files concurrently into `target_folder` (order preserved).

    Rate limiting is enforced per NCBI realm by the shared client, so concurrency is bounded
    by `max_workers` plus the realm token buckets. All downloads are attempted; if any fail,
    a single `DownloadError` is raised afterwards listing every failure, with
    `completed_paths` holding the files that did succeed.
    """
    urls = list(dict.fromkeys(urls))
    names = [download_filename(url) for url in urls]
    if len({name.casefold() for name in names}) != len(names):
        raise DataError(
            "Distinct download URLs have colliding filenames",
            hint="Download these files into separate directories",
        )
    target = Path(target_folder)
    target.mkdir(parents=True, exist_ok=True)
    if not urls:
        return []

    def _one(url: str) -> Path:
        fname = download_filename(url)
        dest_file = target / fname
        client.download_file(url, dest_file)
        return _unpack_downloaded(dest_file, target) if unpack else dest_file

    results: dict[int, Path] = {}
    failures: list[tuple[str, str]] = []
    workers = max(1, min(max_workers, len(urls)))
    reporter = get_reporter()
    with (
        reporter.task(
            "GEO",
            "download_files",
            f"Downloading {len(urls)} file(s) with {workers} worker(s)",
            target=str(target),
        ) as task,
        ThreadPoolExecutor(max_workers=workers) as pool,
    ):
        future_map = {pool.submit(_one, url): idx for idx, url in enumerate(urls)}
        for done, fut in enumerate(as_completed(future_map), start=1):
            idx = future_map[fut]
            try:
                results[idx] = fut.result()
            except Exception as exc:  # noqa: BLE001 - aggregated below
                failures.append((urls[idx], str(exc)))
            task.step(f"{done}/{len(urls)} file(s) finished", current=done, total=len(urls))
        task.complete(f"Downloaded {len(results)}/{len(urls)} file(s) to {target}")

    ordered = [results[i] for i in sorted(results)]
    if failures:
        detail = "; ".join(f"{u.split('/')[-1]}: {err.splitlines()[0]}" for u, err in failures)
        raise DownloadError(
            failures[0][0],
            f"{len(failures)} of {len(urls)} file(s) failed: {detail}",
            completed_paths=list(ordered),
        )
    return ordered


def _inspect_urls_parallel(
    client: HttpClient,
    urls_and_sources: list[tuple[str, str]],
    check_sizes: bool = True,
) -> list[NCBIFileInfo]:
    """Inspect multiple NCBI file URLs in parallel via HTTP HEAD."""
    if not urls_and_sources:
        return []

    if not check_sizes:
        results = []
        for url, source in urls_and_sources:
            fname = url.rstrip("/").split("/")[-1]
            if "file=" in url:
                fname = url.split("file=")[-1].split("&")[0]
            results.append(
                NCBIFileInfo(
                    name=fname,
                    url=url,
                    file_type=infer_file_type(fname),
                    source=source,
                )
            )
        return results

    def _check_one(item: tuple[str, str]) -> NCBIFileInfo | None:
        url, source = item
        info = client.inspect_url(url, source=source)
        # Filter out speculative URLs (like RNAseq counts) if they don't exist
        if source in ("ncbi_rnaseq_counts", "series_matrix") and not info.get("exists", False):
            return None
        return NCBIFileInfo(
            name=info["name"],
            url=info["url"],
            size_bytes=info["size_bytes"],
            size_human=info["size_human"],
            last_modified=info["last_modified"],
            file_type=info["file_type"],
            source=info["source"],
            exists=bool(info["exists"]),
        )

    with ThreadPoolExecutor(max_workers=min(6, len(urls_and_sources))) as pool:
        inspected = list(pool.map(_check_one, urls_and_sources))

    return [item for item in inspected if item is not None]
