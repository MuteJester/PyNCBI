"""NCBI BLAST URL API for PyNCBI (sequence similarity search as an asynchronous job).

The module is named `blast_search` so the headline function can be `PyNCBI.blast()` without a
module/function name collision on the `PyNCBI.domains` package.

BLAST is the one NCBI service that is genuinely asynchronous: you submit a query, get a
Request ID (RID), poll until it is ready, then fetch the results. This module wraps that
lifecycle while respecting NCBI's etiquette (a dedicated `blast` rate-limiter realm allowing
one request per 10 seconds, and a polling floor of 10 seconds against real NCBI hosts):

- `submit_blast`: start a search, get a `BlastJob` with its RID and time estimate
- `check_blast`: poll one RID's status
- `fetch_blast`: parse finished results into a `BlastResult`
- `blast`: submit, poll and fetch in one blocking call (or `wait=False` to just submit)
- `delete_blast`: release a job on NCBI
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from PyNCBI.core.config import get_config
from PyNCBI.core.exceptions import DataError, NoDataAvailableError
from PyNCBI.core.formatter import save_data
from PyNCBI.core.reporter import get_reporter
from PyNCBI.io.http_client import get_default_client

if TYPE_CHECKING:
    from PyNCBI.core.protocols import HttpClient

# program -> default database
BLAST_PROGRAMS: dict[str, str] = {
    "blastn": "nt",
    "blastp": "nr",
    "blastx": "nr",
    "tblastn": "nt",
    "tblastx": "nt",
}
BLAST_STATUSES: tuple[str, ...] = ("waiting", "ready", "unknown", "failed")
POLL_FLOOR_SECONDS = 10.0
DEFAULT_POLL_INTERVAL = 60.0
DEFAULT_TIMEOUT = 900.0

_QBLAST_FIELD_RE = re.compile(r"^\s*([A-Za-z_]+)\s*=\s*(.+?)\s*$")
_NCBI_HOST_RE = re.compile(r"ncbi\.nlm\.nih\.gov", re.I)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _blast_url() -> str:
    return get_config().NCBI_BLAST_URL


def _poll_floor(url: str) -> float:
    """Minimum seconds between polls: NCBI asks for >=10s; local stand-ins are unthrottled."""
    return POLL_FLOOR_SECONDS if _NCBI_HOST_RE.search(url) else 0.0


def _parse_qblast_info(text: str) -> dict[str, str]:
    """Merge every `QBlastInfoBegin ... QBlastInfoEnd` key/value block in a BLAST response.

    NCBI splits a finished job across two blocks (`Status=READY` in one, `ThereAreHits=yes` in
    the next) inside its HTML status page, so all blocks must be read, not just the first.
    """
    fields: dict[str, str] = {}
    inside = False
    for line in text.splitlines():
        if "QBlastInfoBegin" in line:
            inside = True
            continue
        if "QBlastInfoEnd" in line:
            inside = False
            continue
        if inside:
            match = _QBLAST_FIELD_RE.match(line)
            if match:
                fields[match.group(1).strip()] = match.group(2).strip()
    return fields


@dataclass
class BlastJob:
    """A submitted BLAST search."""

    rid: str
    program: str = ""
    database: str = ""
    status: str = "waiting"
    there_are_hits: bool | None = None
    estimated_seconds: int | None = None
    submitted_at: str = ""
    message: str = ""

    @property
    def url(self) -> str:
        return f"https://blast.ncbi.nlm.nih.gov/Blast.cgi?CMD=Get&RID={self.rid}"

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rid": self.rid,
            "program": self.program,
            "database": self.database,
            "status": self.status,
            "there_are_hits": self.there_are_hits,
            "estimated_seconds": self.estimated_seconds,
            "submitted_at": self.submitted_at,
            "message": self.message,
            "url": self.url,
        }

    def to_markdown(self) -> str:
        lines = [f"# BLAST job `{self.rid}`", ""]
        lines.append(
            f"- **Status:** {self.status}"
            + (f" (hits: {self.there_are_hits})" if self.there_are_hits is not None else "")
        )
        if self.program:
            lines.append(f"- **Search:** {self.program} vs {self.database}")
        if self.estimated_seconds is not None:
            lines.append(f"- **Estimated time:** ~{self.estimated_seconds}s")
        lines.append(f"- **Follow:** {self.url}")
        lines.append("")
        lines.append(f"Fetch results later with `pyncbi blast --rid {self.rid}`.")
        return "\n".join(lines)

    def to_text(self) -> str:
        return self.to_markdown().replace("`", "").replace("**", "").replace("# ", "")


@dataclass
class BlastHit:
    """One BLAST hit, summarized by its best (highest-scoring) HSP."""

    hit_num: int
    accession: str = ""
    hit_id: str = ""
    title: str = ""
    length: int = 0
    bit_score: float | None = None
    score: int | None = None
    evalue: float | None = None
    identity: int | None = None
    align_len: int | None = None
    gaps: int | None = None
    query_from: int | None = None
    query_to: int | None = None
    hit_from: int | None = None
    hit_to: int | None = None
    query_frame: int | None = None
    hit_frame: int | None = None
    hsp_count: int = 0

    @property
    def percent_identity(self) -> float | None:
        if not self.identity or not self.align_len:
            return None
        return 100.0 * self.identity / self.align_len

    @property
    def query_coverage(self) -> int | None:
        if self.query_from is None or self.query_to is None:
            return None
        return abs(self.query_to - self.query_from) + 1

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        data = asdict(self)
        data["percent_identity"] = self.percent_identity
        data["query_coverage"] = self.query_coverage
        return data


@dataclass
class BlastResult:
    """Parsed BLAST results."""

    rid: str = ""
    program: str = ""
    version: str = ""
    database: str = ""
    query_id: str = ""
    query_def: str = ""
    query_len: int = 0
    hits: list[BlastHit] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    saved_path: Path | None = None

    @property
    def hit_count(self) -> int:
        return len(self.hits)

    def hits_df(self) -> pd.DataFrame:
        """Hits as a DataFrame indexed by accession, best HSP per hit."""
        columns = [
            "hit_num",
            "title",
            "length",
            "bit_score",
            "score",
            "evalue",
            "percent_identity",
            "identity",
            "align_len",
            "gaps",
            "query_from",
            "query_to",
            "hit_from",
            "hit_to",
            "query_coverage",
            "hsp_count",
            "hit_id",
        ]
        if not self.hits:
            return pd.DataFrame(columns=columns, index=pd.Index([], name="accession"))
        rows = [h.to_dict() for h in self.hits]
        df = pd.DataFrame(rows)
        df = df.set_index("accession")
        df.index.name = "accession"
        return df[[c for c in columns if c in df.columns]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rid": self.rid,
            "program": self.program,
            "version": self.version,
            "database": self.database,
            "query_id": self.query_id,
            "query_def": self.query_def,
            "query_len": self.query_len,
            "hit_count": self.hit_count,
            "hits": [h.to_dict() for h in self.hits],
            "stats": dict(self.stats),
            "saved_path": str(self.saved_path) if self.saved_path else None,
        }

    def to_markdown(self, *, max_hits: int = 20) -> str:
        header = "# BLAST results" + (f" (`{self.rid}`)" if self.rid else "")
        lines = [header, ""]
        lines.append(
            f"- **Search:** {self.version or self.program} vs `{self.database}` | "
            f"**Query:** {self.query_def or self.query_id} ({self.query_len:,} residues) | "
            f"**Hits:** {self.hit_count}"
        )
        if not self.hits:
            lines += ["", "_No hits._"]
            return "\n".join(lines)
        lines += [
            "",
            "| # | Accession | Description | Bit score | E-value | Identity | Coverage |",
            "| :-- | :-- | :-- | --: | --: | --: | --: |",
        ]
        for hit in self.hits[:max_hits]:
            pid = f"{hit.percent_identity:.1f}%" if hit.percent_identity is not None else ""
            evalue = f"{hit.evalue:.2g}" if hit.evalue is not None else ""
            lines.append(
                f"| {hit.hit_num} | `{hit.accession}` | {hit.title[:60]} | "
                f"{hit.bit_score if hit.bit_score is not None else ''} | {evalue} | {pid} | "
                f"{hit.query_coverage or ''} |"
            )
        if self.hit_count > max_hits:
            lines.append("")
            lines.append(f"*Showing {max_hits} of {self.hit_count} hits.*")
        return "\n".join(lines)

    def to_text(self) -> str:
        return self.to_markdown().replace("`", "").replace("**", "").replace("# ", "")


def _text(elem: ET.Element | None) -> str:
    return (elem.text or "").strip() if elem is not None else ""


def _int(elem: ET.Element | None) -> int | None:
    raw = _text(elem)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _float(elem: ET.Element | None) -> float | None:
    raw = _text(elem)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_blast_xml(xml_text: str, *, rid: str = "") -> BlastResult:
    """Parse NCBI BLAST XML (`BlastOutput`) into a `BlastResult`.

    Each hit is summarized by its best HSP (highest bit score) while `hsp_count` records how
    many alignments that hit had.
    """
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as exc:
        snippet = xml_text.strip()[:160].replace("\n", " ")
        raise DataError(
            f"Could not parse the BLAST response as XML: {exc}",
            hint=f"NCBI returned: {snippet!r}. Check the RID with `pyncbi blast --rid <RID>` or resubmit.",
        ) from exc
    if root.tag != "BlastOutput":
        raise DataError(
            f"Unexpected BLAST response root element '{root.tag}' (expected BlastOutput)",
            hint="The job may have expired or failed; check its status before fetching results",
        )

    result = BlastResult(
        rid=rid,
        program=_text(root.find("BlastOutput_program")),
        version=_text(root.find("BlastOutput_version")),
        database=_text(root.find("BlastOutput_db")),
        query_id=_text(root.find("BlastOutput_query-ID")),
        query_def=_text(root.find("BlastOutput_query-def")),
        query_len=_int(root.find("BlastOutput_query-len")) or 0,
    )

    for hit_elem in root.findall(".//Iteration/Iteration_hits/Hit"):
        hsps = hit_elem.findall("Hit_hsps/Hsp")
        best = None
        best_score = None
        for hsp in hsps:
            score = _float(hsp.find("Hsp_bit-score"))
            if best is None or (score is not None and (best_score is None or score > best_score)):
                best, best_score = hsp, score
        hit = BlastHit(
            hit_num=_int(hit_elem.find("Hit_num")) or 0,
            accession=_text(hit_elem.find("Hit_accession")),
            hit_id=_text(hit_elem.find("Hit_id")),
            title=_text(hit_elem.find("Hit_def")),
            length=_int(hit_elem.find("Hit_len")) or 0,
            hsp_count=len(hsps),
        )
        if best is not None:
            hit.bit_score = _float(best.find("Hsp_bit-score"))
            hit.score = _int(best.find("Hsp_score"))
            hit.evalue = _float(best.find("Hsp_evalue"))
            hit.identity = _int(best.find("Hsp_identity"))
            hit.align_len = _int(best.find("Hsp_align-len"))
            hit.gaps = _int(best.find("Hsp_gaps"))
            hit.query_from = _int(best.find("Hsp_query-from"))
            hit.query_to = _int(best.find("Hsp_query-to"))
            hit.hit_from = _int(best.find("Hsp_hit-from"))
            hit.hit_to = _int(best.find("Hsp_hit-to"))
            hit.query_frame = _int(best.find("Hsp_query-frame"))
            hit.hit_frame = _int(best.find("Hsp_hit-frame"))
        result.hits.append(hit)

    stats_elem = root.find(".//Iteration/Iteration_stat/Statistics")
    if stats_elem is not None:
        result.stats = {
            "db_num": _int(stats_elem.find("Statistics_db-num")),
            "db_len": _int(stats_elem.find("Statistics_db-len")),
            "eff_space": _float(stats_elem.find("Statistics_eff-space")),
            "hsp_len": _int(stats_elem.find("Statistics_hsp-len")),
        }
    return result


def _read_query(query: str | Path) -> str:
    """A sequence, a FASTA block, an accession, or a path to a file holding any of those."""
    text = str(query)
    try:
        candidate = Path(text)
        if len(text) < 4096 and candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return text.strip()


def submit_blast(
    query: str | Path,
    *,
    program: str = "blastn",
    database: str | None = None,
    megablast: bool = True,
    evalue: float | None = None,
    hitlist_size: int = 50,
    word_size: int | None = None,
    entrez_query: str | None = None,
    http_client: HttpClient | None = None,
) -> BlastJob:
    """Submit a BLAST search and return its job (does not wait for results).

    Args:
        query: Nucleotide/protein sequence, FASTA text, an accession, or a path to a file
        program: 'blastn', 'blastp', 'blastx', 'tblastn' or 'tblastx'
        database: Target database (defaults per program: nt for nucleotide, nr for protein)
        megablast: For `blastn`, use the faster megablast algorithm (default True)
        evalue: Expect-value threshold
        hitlist_size: Maximum hits to keep (default 50)
        word_size: Optional word size
        entrez_query: Optional Entrez filter, e.g. ``"Homo sapiens"[organism]``
        http_client: Optional HTTP client
    """
    prog = program.strip().lower()
    if prog not in BLAST_PROGRAMS:
        raise ValueError(f"Unknown BLAST program '{program}'; choose from {sorted(BLAST_PROGRAMS)}")
    client = http_client or get_default_client()
    cfg = get_config()
    db = database or BLAST_PROGRAMS[prog]
    params: dict[str, Any] = {
        "CMD": "Put",
        "PROGRAM": prog,
        "DATABASE": db,
        "QUERY": _read_query(query),
        "HITLIST_SIZE": hitlist_size,
        "FORMAT_TYPE": "XML",
        "tool": "PyNCBI",
    }
    if prog == "blastn":
        params["MEGABLAST"] = "on" if megablast else "off"
    if evalue is not None:
        params["EXPECT"] = evalue
    if word_size is not None:
        params["WORD_SIZE"] = word_size
    if entrez_query:
        params["ENTREZ_QUERY"] = entrez_query
    if cfg.email:
        params["email"] = cfg.email

    reporter = get_reporter()
    with reporter.task(
        "BLAST", "submit_blast", f"Submitting {prog} search against {db}", target=prog
    ) as task:
        text = client.get_text(_blast_url(), params=params)
        info = _parse_qblast_info(text)
        rid = info.get("RID", "")
        if not rid:
            raise DataError(
                "NCBI BLAST did not return a Request ID (RID)",
                hint="The query may be malformed or BLAST may be refusing new jobs; check "
                "https://blast.ncbi.nlm.nih.gov/ and retry",
            )
        rtoe = info.get("RTOE")
        job = BlastJob(
            rid=rid,
            program=prog,
            database=db,
            status="waiting",
            estimated_seconds=int(rtoe) if rtoe and rtoe.isdigit() else None,
            submitted_at=_now_iso(),
        )
        estimate = f", ready in ~{job.estimated_seconds}s" if job.estimated_seconds else ""
        task.complete(f"Submitted {prog} job {rid}{estimate}")
        return job


def _status_of(rid: str, client: HttpClient) -> tuple[str, bool | None]:
    """Fetch one RID's `(status, there_are_hits)` from the BLAST SearchInfo endpoint."""
    text = client.get_text(
        _blast_url(),
        params={"CMD": "Get", "RID": rid, "FORMAT_OBJECT": "SearchInfo", "tool": "PyNCBI"},
    )
    info = _parse_qblast_info(text)
    raw_status = info.get("Status", "").strip().upper()
    status = {"WAITING": "waiting", "READY": "ready", "UNKNOWN": "unknown", "FAILED": "failed"}.get(
        raw_status, "unknown"
    )
    hits_raw = info.get("ThereAreHits")
    there_are_hits = None if hits_raw is None else hits_raw.strip().lower() == "yes"
    return status, there_are_hits


def check_blast(rid: str, *, http_client: HttpClient | None = None) -> BlastJob:
    """Check the status of a submitted BLAST job."""
    client = http_client or get_default_client()
    status, there_are_hits = _status_of(rid.strip(), client)
    message = {
        "waiting": "Still running; poll again in a minute.",
        "ready": "Results are ready to fetch.",
        "unknown": "NCBI does not recognise this RID (jobs expire after about 24 hours).",
        "failed": "The search failed on NCBI's side.",
    }.get(status, "")
    return BlastJob(rid=rid.strip(), status=status, there_are_hits=there_are_hits, message=message)


def fetch_blast(
    rid: str,
    *,
    save_to: str | Path | None = None,
    http_client: HttpClient | None = None,
) -> BlastResult:
    """Fetch and parse the results of a finished BLAST job."""
    client = http_client or get_default_client()
    clean = rid.strip()
    with get_reporter().task(
        "BLAST", "fetch_blast", f"Fetching BLAST results for {clean}", target=clean
    ) as task:
        xml_text = client.get_text(
            _blast_url(),
            params={"CMD": "Get", "RID": clean, "FORMAT_TYPE": "XML", "tool": "PyNCBI"},
        )
        result = parse_blast_xml(xml_text, rid=clean)
        if save_to is not None and result.hits:
            result.saved_path = save_data(result.hits_df(), save_to)
            task.step(f"Saved {result.hit_count} hit(s) to {result.saved_path}")
        task.complete(f"{clean}: {result.hit_count} hit(s) against {result.database or 'database'}")
        return result


def delete_blast(rid: str, *, http_client: HttpClient | None = None) -> bool:
    """Ask NCBI to release a BLAST job (frees shared resources; jobs also expire on their own)."""
    client = http_client or get_default_client()
    client.get_text(_blast_url(), params={"CMD": "Delete", "RID": rid.strip(), "tool": "PyNCBI"})
    return True


def blast(
    query: str | Path,
    *,
    program: str = "blastn",
    database: str | None = None,
    megablast: bool = True,
    evalue: float | None = None,
    hitlist_size: int = 50,
    word_size: int | None = None,
    entrez_query: str | None = None,
    wait: bool = True,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    timeout: float = DEFAULT_TIMEOUT,
    save_to: str | Path | None = None,
    http_client: HttpClient | None = None,
) -> BlastResult | BlastJob:
    """Run a BLAST search end to end: submit, poll until ready, and return the parsed results.

    Args:
        query: Sequence, FASTA text, accession, or a path to a file holding one
        program / database / megablast / evalue / hitlist_size / word_size / entrez_query:
            Passed to `submit_blast`
        wait: If False, return the `BlastJob` immediately after submission
        poll_interval: Seconds between status checks (clamped to >=10s against NCBI, per their
            usage policy; default 60)
        timeout: Give up after this many seconds and raise `TimeoutError` naming the RID, so
            the job can still be fetched later
        save_to: Optional path for the hit table
        http_client: Optional HTTP client

    Raises:
        TimeoutError: Still running when `timeout` elapsed (the message carries the RID)
        DataError: NCBI reports the job as unknown/expired or failed
        NoDataAvailableError: The search finished with no hits
    """
    client = http_client or get_default_client()
    job = submit_blast(
        query,
        program=program,
        database=database,
        megablast=megablast,
        evalue=evalue,
        hitlist_size=hitlist_size,
        word_size=word_size,
        entrez_query=entrez_query,
        http_client=client,
    )
    if not wait:
        return job

    interval = max(float(poll_interval), _poll_floor(_blast_url()))
    deadline = time.monotonic() + float(timeout)
    reporter = get_reporter()
    with reporter.task(
        "BLAST", "blast", f"Waiting for BLAST job {job.rid}", target=job.rid
    ) as task:
        attempt = 0
        while True:
            status, there_are_hits = _status_of(job.rid, client)
            attempt += 1
            if status == "ready":
                job.status, job.there_are_hits = status, there_are_hits
                if there_are_hits is False:
                    raise NoDataAvailableError(
                        job.rid, detail=f"the {job.program} search finished with no hits"
                    )
                task.complete(f"Job {job.rid} ready after {attempt} poll(s)")
                break
            if status in ("unknown", "failed"):
                raise DataError(
                    f"BLAST job {job.rid} reported status '{status}' (expired, deleted, or failed)",
                    hint="Jobs expire after about 24 hours; resubmit the search",
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"BLAST job {job.rid} was still running after {timeout:.0f}s. "
                    f"It is not lost: fetch it later with `pyncbi blast --rid {job.rid}`."
                )
            task.step(
                f"Still running (poll {attempt}); next check in {min(interval, remaining):.0f}s"
            )
            if interval > 0:
                time.sleep(min(interval, remaining))

    return fetch_blast(job.rid, save_to=save_to, http_client=client)
