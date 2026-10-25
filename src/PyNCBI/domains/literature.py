"""PubMed Central full-text engine for PyNCBI.

- `resolve_pmc_id`: PMID <-> PMCID resolution via E-Utilities `elink`
- `fetch_pmc_text`: Open-access full text as classified sections (Methods, Results,
  Data Availability, ...) ready for agents to read a study's exact protocol
- `attach_full_text`: Enrich PubMedArticle objects with their PMC full text / Methods
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from PyNCBI.core.config import get_config
from PyNCBI.core.models import PMCFullText, PubMedArticle
from PyNCBI.core.reporter import get_reporter
from PyNCBI.io.http_client import get_default_client
from PyNCBI.parsing.pmc_parser import parse_pmc_article_xml

if TYPE_CHECKING:
    from PyNCBI.core.protocols import HttpClient

_PMC_RE = re.compile(r"^PMC\d+$", re.I)
_PMID_RE = re.compile(r"^\d+$")

METHODS_SECTIONS: tuple[str, ...] = ("methods", "data_availability")


def _eutils(endpoint: str) -> str:
    return f"{get_config().NCBI_EUTILS_BASE_URL}/{endpoint}"


def _normalize_id(value: str) -> str:
    v = str(value).strip()
    for prefix in ("PMID:", "pmid:", "PMCID:", "pmcid:"):
        if v.startswith(prefix):
            v = v[len(prefix) :].strip()
    return v


def resolve_pmc_id(
    pmid_or_pmcid: str,
    *,
    http_client: HttpClient | None = None,
) -> tuple[str, str]:
    """Resolve a PMID or PMCID into `(pmid, pmcid)`; missing parts are empty strings.

    A PMCID input is returned as-is (its PMID is filled in later from the article XML).
    A PMID is linked to PMC via `elink` (`pubmed_pmc`); '' when the paper is not in PMC.
    """
    client = http_client or get_default_client()
    ident = _normalize_id(pmid_or_pmcid)
    if _PMC_RE.match(ident):
        return "", ident.upper()
    if not _PMID_RE.match(ident):
        raise ValueError(f"'{pmid_or_pmcid}' is not a PMID or PMCID")

    payload = client.get_json(
        _eutils("elink.fcgi"),
        params={"dbfrom": "pubmed", "db": "pmc", "id": ident, "retmode": "json"},
    )
    for linkset in payload.get("linksets", []) or []:
        for ldb in linkset.get("linksetdbs", []) or []:
            if ldb.get("linkname") == "pubmed_pmc" and ldb.get("links"):
                return ident, f"PMC{ldb['links'][0]}"
    return ident, ""


def fetch_pmc_text(
    pmid_or_pmcid: str,
    *,
    sections: list[str] | tuple[str, ...] | None = None,
    http_client: HttpClient | None = None,
) -> PMCFullText:
    """Fetch the open-access full text of an article from PubMed Central as sections.

    Args:
        pmid_or_pmcid: PMID (e.g. '32433946'), 'PMID:...', or PMCID (e.g. 'PMC7236739')
        sections: Optional filter: kinds ('methods', 'results', 'discussion',
            'data_availability', 'introduction', 'supplementary') and/or title substrings.
            `None` keeps every section.
        http_client: Optional HTTP client

    Returns:
        `PMCFullText`; `is_open_access=False` with an explanatory `notes` when PMC has no
        redistributable XML for the article (or the PMID is not in PMC at all).
    """
    client = http_client or get_default_client()
    reporter = get_reporter()
    with reporter.task(
        "PMC",
        "fetch_pmc_text",
        f"Fetching PMC full text for {pmid_or_pmcid}",
        target=str(pmid_or_pmcid),
    ) as task:
        pmid, pmcid = resolve_pmc_id(pmid_or_pmcid, http_client=client)
        if not pmcid:
            task.complete(f"PMID {pmid} has no PMC record")
            return PMCFullText(
                pmcid="",
                pmid=pmid,
                is_open_access=False,
                notes="No PMC record is linked to this PMID (article not deposited in PubMed Central).",
            )
        xml_text = client.get_text(
            _eutils("efetch.fcgi"), params={"db": "pmc", "id": pmcid, "retmode": "xml"}
        )
        article = parse_pmc_article_xml(xml_text)
        if not article.pmcid:
            article.pmcid = pmcid
        if pmid and not article.pmid:
            article.pmid = pmid
        if sections:
            article.section_filter = list(sections)
        n_methods = sum(1 for s in article.sections if s.kind == "methods")
        task.complete(
            f"{article.pmcid}: {'open access' if article.is_open_access else 'not open access'}, "
            f"{len(article.sections)} section(s), {n_methods} methods section(s), "
            f"{len(article.selected_sections())} selected"
        )
        return article


def attach_full_text(
    articles: list[PubMedArticle],
    *,
    sections: list[str] | tuple[str, ...] | None = METHODS_SECTIONS,
    http_client: HttpClient | None = None,
) -> list[PubMedArticle]:
    """Attach PMC full text (filtered to `sections`) to each PubMedArticle in place."""
    client = http_client or get_default_client()
    for art in articles:
        ident = art.pmc_id or art.pmid
        if not ident:
            continue
        try:
            art.full_text = fetch_pmc_text(ident, sections=sections, http_client=client)
        except Exception as exc:  # noqa: BLE001 - enrichment must never break the abstract fetch
            art.full_text = PMCFullText(
                pmcid=art.pmc_id,
                pmid=art.pmid,
                is_open_access=False,
                notes=f"PMC fetch failed: {exc}",
            )
    return articles
