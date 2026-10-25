"""NCBI Entrez Search, PubMed, Gene & Cross-Database Linker for PyNCBI.

Provides clean, rate-limited programmatic access to NCBI E-Utilities:
- `search_geo`: Search GEO datasets (`db=gds`) with structured filters and GSE resolution
- `search_pubmed` / `fetch_pubmed`: Search PubMed and parse full article abstracts
- `search_gene` / `fetch_genes`: Search and retrieve NCBI Gene records
- `search_ncbi`: Generic search across SRA, BioProject, BioSample, ClinVar, etc.
- `link_accession`: Discover cross-database links (GSE <-> PMID <-> BioProject <-> SRA)
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyNCBI.core.config import get_config
from PyNCBI.core.formatter import save_data
from PyNCBI.core.models import (
    BioProjectRecord,
    BioSampleRecord,
    FASTASequence,
    GeneRecord,
    GEOSearchHit,
    NucleotideRecord,
    PubMedArticle,
    TaxonRecord,
)
from PyNCBI.core.reporter import get_reporter
from PyNCBI.core.tables import Table, from_records, iter_records, project, table
from PyNCBI.io.http_client import get_default_client, normalize_ncbi_url
from PyNCBI.parsing.tabular import parse_delimited_table

if TYPE_CHECKING:
    from collections.abc import Callable

    from PyNCBI.core.protocols import HttpClient


def _eutils_url(endpoint: str) -> str:
    cfg = get_config()
    return f"{cfg.NCBI_EUTILS_BASE_URL}/{endpoint}"


ESEARCH_PAGE_SIZE = 1000
ESUMMARY_BATCH_SIZE = 500
EFETCH_BATCH_SIZE = 200


def esearch_ids(
    db: str,
    term: str,
    *,
    limit: int | None = 20,
    sort: str | None = None,
    http_client: HttpClient | None = None,
) -> tuple[list[str], int]:
    """Run `esearch` with transparent `retstart` pagination.

    Args:
        db: Entrez database
        term: Entrez query
        limit: Maximum UIDs to return; `None` fetches every match (pages of 1000)
        sort: Optional Entrez sort key (e.g. 'relevance', 'pub_date')
        http_client: Optional HTTP client

    Returns:
        `(uids, total_count)` where `total_count` is NCBI's full hit count.
    """
    client = http_client or get_default_client()
    ids: list[str] = []
    total = 0
    retstart = 0
    while True:
        remaining = None if limit is None else max(0, limit - len(ids))
        if remaining == 0:
            break
        retmax = ESEARCH_PAGE_SIZE if remaining is None else min(ESEARCH_PAGE_SIZE, remaining)
        params: dict[str, Any] = {
            "db": db,
            "term": term,
            "retmax": retmax,
            "retstart": retstart,
            "retmode": "json",
        }
        if sort:
            params["sort"] = sort
        resp = client.get_json(_eutils_url("esearch.fcgi"), params=params)
        result = resp.get("esearchresult", {}) or {}
        page = [str(i) for i in result.get("idlist", []) or []]
        try:
            total = int(result.get("count", len(page)) or 0)
        except (TypeError, ValueError):
            total = len(page)
        ids.extend(page)
        retstart += len(page)
        if not page or len(page) < retmax or retstart >= total:
            break
    if limit is not None:
        ids = ids[:limit]
    return ids, total


def esummary_docs(
    db: str,
    uids: list[str],
    *,
    http_client: HttpClient | None = None,
    batch_size: int = ESUMMARY_BATCH_SIZE,
) -> dict[str, dict[str, Any]]:
    """Fetch `esummary` documents for many UIDs in batches (POST-promoted automatically)."""
    client = http_client or get_default_client()
    docs: dict[str, dict[str, Any]] = {}
    for i in range(0, len(uids), batch_size):
        batch = uids[i : i + batch_size]
        if not batch:
            continue
        resp = client.get_json(
            _eutils_url("esummary.fcgi"),
            params={"db": db, "id": ",".join(batch), "retmode": "json"},
        )
        result = resp.get("result", {}) or {}
        for uid in batch:
            doc = result.get(str(uid))
            if isinstance(doc, dict):
                docs[str(uid)] = doc
    return docs


# ---------------------------------------------------------------------------
# Structured record parsers (pure)
# ---------------------------------------------------------------------------


def _xml_text(elem: ET.Element | None) -> str:
    return "".join(elem.itertext()).strip() if elem is not None else ""


def parse_biosample_summary(doc: dict[str, Any]) -> BioSampleRecord:
    """Flatten an `esummary db=biosample` document (incl. its embedded XML) into a record."""
    attributes: dict[str, str] = {}
    identifiers: dict[str, str] = {}
    owner = ""
    pub_date = str(doc.get("publicationdate") or doc.get("date") or "")
    sampledata = str(doc.get("sampledata") or "")
    if sampledata.strip().startswith("<"):
        try:
            root_el = ET.fromstring(sampledata)
        except ET.ParseError:
            root_el = None
        if root_el is not None:
            for attr in root_el.findall(".//Attributes/Attribute"):
                name = attr.get("harmonized_name") or attr.get("attribute_name") or ""
                if name:
                    attributes[name] = _xml_text(attr)
            for id_el in root_el.findall(".//Ids/Id"):
                db_name = id_el.get("db") or id_el.get("db_label") or ""
                if db_name:
                    identifiers[db_name] = _xml_text(id_el)
            owner = _xml_text(root_el.find(".//Owner/Name"))
            pub_date = root_el.get("publication_date", pub_date)[:10] or pub_date
    if not identifiers:
        for part in str(doc.get("identifiers", "")).split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                identifiers[k.strip()] = v.strip()
    return BioSampleRecord(
        accession=str(doc.get("accession", "")),
        uid=str(doc.get("uid", "")),
        title=str(doc.get("title", "")),
        organism=str(doc.get("organism", "")),
        taxid=str(doc.get("taxonomy", "")),
        package=str(doc.get("package", "")),
        publication_date=pub_date,
        owner=owner,
        gsm_id=identifiers.get("GEO", ""),
        sra_sample=identifiers.get("SRA", ""),
        attributes=attributes,
        identifiers=identifiers,
    )


def parse_bioproject_summary(doc: dict[str, Any]) -> BioProjectRecord:
    """Flatten an `esummary db=bioproject` document into a record."""
    relevance = [
        key.replace("relevance_", "")
        for key, val in doc.items()
        if key.startswith("relevance_") and str(val).strip().lower() in ("yes", "true", "1")
    ]
    return BioProjectRecord(
        accession=str(doc.get("project_acc", "")),
        uid=str(doc.get("uid", "")),
        title=str(doc.get("project_title", "")),
        name=str(doc.get("project_name", "")),
        description=str(doc.get("project_description", "")),
        project_type=str(doc.get("project_type", "")),
        data_type=str(doc.get("project_data_type", "")),
        target_scope=str(doc.get("project_target_scope", "")),
        target_material=str(doc.get("project_target_material", "")),
        method=str(doc.get("project_methodtype", "") or doc.get("project_method", "")),
        organism=str(doc.get("organism_name", "")),
        taxid=str(doc.get("taxid", "")),
        registration_date=str(doc.get("registration_date", "")).replace(" 00:00", ""),
        relevance=relevance,
    )


def parse_nucleotide_summary(doc: dict[str, Any], *, db: str = "nuccore") -> NucleotideRecord:
    """Flatten an `esummary db=nuccore|protein` document into a record."""
    try:
        length = int(doc.get("slen", 0) or 0)
    except (TypeError, ValueError):
        length = 0
    return NucleotideRecord(
        accession=str(doc.get("accessionversion") or doc.get("caption") or ""),
        uid=str(doc.get("uid", "")),
        title=str(doc.get("title", "")),
        organism=str(doc.get("organism", "")),
        taxid=str(doc.get("taxid", "")),
        length=length,
        moltype=str(doc.get("moltype", "")),
        biomol=str(doc.get("biomol", "")),
        topology=str(doc.get("topology", "")),
        source_db=str(doc.get("sourcedb", "")),
        completeness=str(doc.get("completeness", "")),
        create_date=str(doc.get("createdate", "")),
        update_date=str(doc.get("updatedate", "")),
        db=db,
    )


def parse_taxonomy_summary(doc: dict[str, Any], *, xml_text: str = "") -> TaxonRecord:
    """Build a TaxonRecord from `esummary db=taxonomy` (+ optional `efetch` XML for lineage)."""
    rec = TaxonRecord(
        taxid=str(doc.get("taxid") or doc.get("uid") or ""),
        scientific_name=str(doc.get("scientificname", "")),
        common_name=str(doc.get("commonname", "")),
        rank=str(doc.get("rank", "")),
        division=str(doc.get("genbankdivision") or doc.get("division") or ""),
    )
    if xml_text:
        try:
            root_el = ET.fromstring(xml_text)
        except ET.ParseError:
            return rec
        taxon = None
        for cand in root_el.iter("Taxon"):
            if _xml_text(cand.find("TaxId")) == rec.taxid or taxon is None:
                taxon = cand
                if _xml_text(cand.find("TaxId")) == rec.taxid:
                    break
        if taxon is not None:
            rec.parent_taxid = _xml_text(taxon.find("ParentTaxId"))
            rec.genetic_code = _xml_text(taxon.find("GeneticCode/GCId"))
            rec.mito_genetic_code = _xml_text(taxon.find("MitoGeneticCode/MGCId"))
            lineage = _xml_text(taxon.find("Lineage"))
            rec.lineage = [x.strip() for x in lineage.split(";") if x.strip()]
            for anc in taxon.findall("LineageEx/Taxon"):
                r = _xml_text(anc.find("Rank"))
                if r and r != "no rank":
                    rec.lineage_ranks[r] = _xml_text(anc.find("ScientificName"))
            if not rec.common_name:
                rec.common_name = _xml_text(taxon.find("OtherNames/GenbankCommonName"))
            rec.synonyms = [_xml_text(x) for x in taxon.findall("OtherNames/Synonym")]
            if not rec.rank:
                rec.rank = _xml_text(taxon.find("Rank"))
    return rec


_STRUCTURED_DBS: dict[str, Callable[[dict[str, Any], str], Any]] = {
    "biosample": lambda d, db: parse_biosample_summary(d),
    "bioproject": lambda d, db: parse_bioproject_summary(d),
    "nuccore": lambda d, db: parse_nucleotide_summary(d, db="nuccore"),
    "nucleotide": lambda d, db: parse_nucleotide_summary(d, db="nuccore"),
    "protein": lambda d, db: parse_nucleotide_summary(d, db="protein"),
    "taxonomy": lambda d, db: parse_taxonomy_summary(d),
}


def fetch_biosamples(
    ids: list[str] | str,
    *,
    http_client: HttpClient | None = None,
) -> list[BioSampleRecord]:
    """Fetch BioSample records by SAMN accession, numeric UID, GSM, or SRS identifier."""
    raw = re.split(r"[,\s]+", ids) if isinstance(ids, str) else [str(i) for i in ids]
    wanted = [r.strip() for r in raw if r.strip()]
    if not wanted:
        return []
    client = http_client or get_default_client()
    uids: list[str] = []
    for item in wanted:
        upper = item.upper()
        if item.isdigit():
            uids.append(item)
        elif upper.startswith("SAMN") and upper[4:].isdigit():
            uids.append(str(int(upper[4:])))  # NCBI BioSample UIDs are the SAMN number
        else:
            found, _ = esearch_ids("biosample", item, limit=5, http_client=client)
            uids.extend(found)
    docs = esummary_docs("biosample", list(dict.fromkeys(uids)), http_client=client)
    return [parse_biosample_summary(docs[u]) for u in uids if u in docs]


def fetch_bioprojects(
    ids: list[str] | str,
    *,
    http_client: HttpClient | None = None,
) -> list[BioProjectRecord]:
    """Fetch BioProject records by PRJNA/PRJEB/PRJDB accession or numeric UID."""
    raw = re.split(r"[,\s]+", ids) if isinstance(ids, str) else [str(i) for i in ids]
    wanted = [r.strip() for r in raw if r.strip()]
    if not wanted:
        return []
    client = http_client or get_default_client()
    uids: list[str] = []
    for item in wanted:
        if item.isdigit():
            uids.append(item)
        else:
            found, _ = esearch_ids(
                "bioproject", f"{item.upper()}[Project Accession]", limit=5, http_client=client
            )
            uids.extend(found)
    docs = esummary_docs("bioproject", list(dict.fromkeys(uids)), http_client=client)
    return [parse_bioproject_summary(docs[u]) for u in uids if u in docs]


def fetch_taxonomy(
    ids_or_names: list[str] | str,
    *,
    http_client: HttpClient | None = None,
) -> list[TaxonRecord]:
    """Fetch Taxonomy records (with full lineage) by TaxID or exact scientific name."""
    raw = (
        ids_or_names.split(",") if isinstance(ids_or_names, str) else [str(i) for i in ids_or_names]
    )
    wanted = [r.strip() for r in raw if r.strip()]
    if not wanted:
        return []
    client = http_client or get_default_client()
    uids: list[str] = []
    for item in wanted:
        if item.isdigit():
            uids.append(item)
        else:
            found, _ = esearch_ids(
                "taxonomy", f"{item}[Scientific Name]", limit=1, http_client=client
            )
            if not found:
                found, _ = esearch_ids(
                    "taxonomy", f"{item}[All Names]", limit=1, http_client=client
                )
            uids.extend(found)
    uids = list(dict.fromkeys(uids))
    if not uids:
        return []
    docs = esummary_docs("taxonomy", uids, http_client=client)
    xml_text = client.get_text(
        _eutils_url("efetch.fcgi"),
        params={"db": "taxonomy", "id": ",".join(uids), "retmode": "xml"},
    )
    per_taxon_xml: dict[str, str] = {}
    try:
        root_el = ET.fromstring(xml_text)
        for taxon in root_el.findall("Taxon"):
            tid = _xml_text(taxon.find("TaxId"))
            if tid:
                per_taxon_xml[tid] = ET.tostring(taxon, encoding="unicode")
    except ET.ParseError:
        pass
    return [
        parse_taxonomy_summary(docs[u], xml_text=f"<TaxaSet>{per_taxon_xml.get(u, '')}</TaxaSet>")
        for u in uids
        if u in docs
    ]


def search_taxonomy(
    query: str,
    *,
    limit: int = 10,
    http_client: HttpClient | None = None,
) -> list[TaxonRecord]:
    """Search NCBI Taxonomy by any name (common, scientific, synonym) and return lineage-aware records."""
    client = http_client or get_default_client()
    q = query.strip()
    if q.isdigit():
        return fetch_taxonomy([q], http_client=client)
    uids, _ = esearch_ids("taxonomy", f"{q}[All Names]", limit=limit, http_client=client)
    return fetch_taxonomy(uids, http_client=client) if uids else []


def search_geo(
    query: str,
    *,
    organism: str | None = None,
    entry_type: str = "gse",
    dataset_type: str | None = None,
    min_samples: int | None = None,
    max_samples: int | None = None,
    limit: int | None = 20,
    http_client: HttpClient | None = None,
) -> list[GEOSearchHit]:
    """Search NCBI GEO (`db=gds`) and return structured GEOSearchHit records.

    Automatically handles NCBI's GDS numeric UID-to-GSE accession translation
    and entry-type filtering.

    Args:
        query: Search query (e.g., 'fibromyalgia methylation', 'breast cancer single cell')
        organism: Optional organism filter (e.g., 'Homo sapiens', 'Mus musculus')
        entry_type: 'gse' (series, default), 'gsm' (samples), 'gpl' (platforms), 'gds', or 'all'
        dataset_type: Optional dataset type shorthand ('rnaseq', 'methylation', 'microarray', 'scrnaseq')
                      or full NCBI DataSet Type string
        min_samples: Minimum number of samples in series
        max_samples: Maximum number of samples in series
        limit: Maximum number of hits to return (default 20; `None` returns every match)
        http_client: Optional HTTP client

    Returns:
        List of GEOSearchHit objects
    """
    client = http_client or get_default_client()
    clauses = [f"({query.strip()})"]

    etype = entry_type.strip().lower()
    if etype in ("gse", "gsm", "gpl", "gds"):
        clauses.append(f'"{etype}"[Filter]')

    if organism:
        clauses.append(f'"{organism.strip()}"[Organism]')

    if dataset_type:
        dt_map = {
            "rnaseq": "expression profiling by high throughput sequencing",
            "rna-seq": "expression profiling by high throughput sequencing",
            "methylation": "methylation profiling by array",
            "microarray": "expression profiling by array",
            "array": "expression profiling by array",
            "chipseq": "genome binding/occupancy profiling by high throughput sequencing",
            "atac": "genome binding/occupancy profiling by high throughput sequencing",
        }
        mapped_dt = dt_map.get(dataset_type.strip().lower(), dataset_type.strip())
        clauses.append(f'"{mapped_dt}"[DataSet Type]')

    if min_samples is not None or max_samples is not None:
        low = min_samples if min_samples is not None else 1
        high = max_samples if max_samples is not None else 1000000
        clauses.append(f"{low}:{high}[Number of Samples]")

    full_term = " AND ".join(clauses)
    reporter = get_reporter()

    with reporter.task(
        "ENTREZ", "search_geo", f"Searching GEO for '{full_term}'", target=query
    ) as task:
        id_list, total = esearch_ids("gds", full_term, limit=limit, http_client=client)
        if not id_list:
            task.complete(f"Found 0 GEO hits for '{query}'")
            return []

        task.step(f"Resolving {len(id_list)} of {total} GDS UID(s) via esummary")
        result_block = esummary_docs("gds", id_list, http_client=client)
        hits: list[GEOSearchHit] = []

        for uid in id_list:
            doc = result_block.get(str(uid))
            if not isinstance(doc, dict):
                continue

            accession = str(doc.get("accession", "")).strip()
            raw_gpl = str(doc.get("gpl", "")).strip()
            platforms = [
                f"GPL{p.strip()}" if not p.strip().startswith("GPL") else p.strip()
                for p in raw_gpl.split(";")
                if p.strip()
            ]

            pmids_raw = doc.get("pubmedids", [])
            pmids = [str(p) for p in pmids_raw] if isinstance(pmids_raw, list) else []

            ftp_link = str(doc.get("ftplink", "")).strip()
            if ftp_link:
                ftp_link = normalize_ncbi_url(ftp_link)

            hits.append(
                GEOSearchHit(
                    accession=accession,
                    gds_uid=str(uid),
                    entry_type=str(doc.get("entrytype", "")),
                    title=str(doc.get("title", "")),
                    summary=str(doc.get("summary", "")),
                    organism=str(doc.get("taxon", "")),
                    dataset_type=str(doc.get("gdstype", "")),
                    platforms=platforms,
                    sample_count=int(doc.get("n_samples", 0) or 0),
                    pubmed_ids=pmids,
                    bioproject_id=str(doc.get("bioproject", "")),
                    supp_file_types=str(doc.get("suppfile", "")),
                    ftp_url=ftp_link,
                    release_date=str(doc.get("pdat", "")),
                )
            )

        task.complete(f"Found {len(hits)} GEO record(s) for '{query}'")
        return hits


def fetch_pubmed(
    pmids: list[str] | str,
    *,
    http_client: HttpClient | None = None,
) -> list[PubMedArticle]:
    """Fetch full PubMed articles (including abstracts, authors, DOIs) by PMID."""
    if isinstance(pmids, str):
        pmid_list = [
            p.strip().replace("PMID:", "") for p in re.split(r"[,\s]+", pmids) if p.strip()
        ]
    else:
        pmid_list = [str(p).strip().replace("PMID:", "") for p in pmids if str(p).strip()]

    if not pmid_list:
        return []

    client = http_client or get_default_client()
    articles: list[PubMedArticle] = []
    for i in range(0, len(pmid_list), EFETCH_BATCH_SIZE):
        batch = pmid_list[i : i + EFETCH_BATCH_SIZE]
        xml_text = client.get_text(
            _eutils_url("efetch.fcgi"),
            params={"db": "pubmed", "id": ",".join(batch), "retmode": "xml"},
        )
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            continue
        articles.extend(_parse_pubmed_articles(root))
    return articles


def _parse_pubmed_articles(root: ET.Element) -> list[PubMedArticle]:
    articles: list[PubMedArticle] = []
    for art_elem in root.findall(".//PubmedArticle"):
        pmid_elem = art_elem.find(".//MedlineCitation/PMID")
        pmid = pmid_elem.text.strip() if pmid_elem is not None and pmid_elem.text else ""

        title_elem = art_elem.find(".//Article/ArticleTitle")
        title = "".join(title_elem.itertext()).strip() if title_elem is not None else ""

        abstract_parts: list[str] = []
        for abs_elem in art_elem.findall(".//Article/Abstract/AbstractText"):
            label = abs_elem.attrib.get("Label", "")
            txt = "".join(abs_elem.itertext()).strip()
            if txt:
                abstract_parts.append(f"{label}: {txt}" if label else txt)
        abstract = "\n\n".join(abstract_parts)

        authors: list[str] = []
        author_details: list[dict[str, str]] = []
        for auth in art_elem.findall(".//Article/AuthorList/Author"):
            last = (auth.findtext("LastName", "") or "").strip()
            fore = (
                (auth.findtext("ForeName", "") or "") or (auth.findtext("Initials", "") or "")
            ).strip()
            collective = (auth.findtext("CollectiveName", "") or "").strip()
            if not last and collective:
                last = collective
            if last:
                authors.append(f"{last} {fore}".strip())
                author_details.append(
                    {
                        "last": last,
                        "fore": fore,
                        "initials": (auth.findtext("Initials", "") or "").strip(),
                        "affiliation": (
                            auth.findtext(".//AffiliationInfo/Affiliation", "") or ""
                        ).strip(),
                    }
                )

        journal = (
            art_elem.findtext(".//Article/Journal/Title", "")
            or art_elem.findtext(".//Article/Journal/ISOAbbreviation", "")
        ).strip()

        year = art_elem.findtext(".//Article/Journal/JournalIssue/PubDate/Year", "")
        month = art_elem.findtext(".//Article/Journal/JournalIssue/PubDate/Month", "")
        pub_date = (
            f"{year} {month}".strip()
            or art_elem.findtext(".//Article/Journal/JournalIssue/PubDate/MedlineDate", "").strip()
        )

        doi = ""
        pmc_id = ""
        for aid in art_elem.findall(".//PubmedData/ArticleIdList/ArticleId"):
            id_type = aid.attrib.get("IdType", "")
            val = (aid.text or "").strip()
            if id_type == "doi":
                doi = val
            elif id_type == "pmc":
                pmc_id = val

        keywords = [
            (kw.text or "").strip()
            for kw in art_elem.findall(".//MedlineCitation/KeywordList/Keyword")
            if (kw.text or "").strip()
        ]

        articles.append(
            PubMedArticle(
                pmid=pmid,
                title=title,
                abstract=abstract,
                authors=authors,
                journal=journal,
                pub_date=pub_date,
                doi=doi,
                pmc_id=pmc_id,
                keywords=keywords,
                author_details=author_details,
            )
        )

    return articles


def search_pubmed(
    query: str,
    *,
    limit: int | None = 10,
    sort: str = "relevance",
    http_client: HttpClient | None = None,
) -> list[PubMedArticle]:
    """Search PubMed and return full PubMedArticle records including abstracts."""
    client = http_client or get_default_client()
    pmids, _ = esearch_ids("pubmed", query.strip(), limit=limit, sort=sort, http_client=client)
    if not pmids:
        return []
    return fetch_pubmed(pmids, http_client=client)


def fetch_genes(
    gene_ids: list[str] | str,
    *,
    http_client: HttpClient | None = None,
) -> list[GeneRecord]:
    """Fetch structured NCBI Gene records by numeric Entrez Gene ID(s)."""
    if isinstance(gene_ids, str):
        id_list = [g.strip() for g in re.split(r"[,\s]+", gene_ids) if g.strip()]
    else:
        id_list = [str(g).strip() for g in gene_ids if str(g).strip()]

    if not id_list:
        return []

    client = http_client or get_default_client()
    result_block = esummary_docs("gene", id_list, http_client=client)
    records: list[GeneRecord] = []

    for gid in id_list:
        doc = result_block.get(str(gid))
        if not isinstance(doc, dict):
            continue

        org_dict = doc.get("organism", {})
        org_name = org_dict.get("scientificname", "") if isinstance(org_dict, dict) else ""

        raw_aliases = str(doc.get("otheraliases", "")).strip()
        aliases = [a.strip() for a in raw_aliases.split(",") if a.strip()]

        raw_desig = str(doc.get("otherdesignations", "")).strip()
        designations = [d.strip() for d in raw_desig.split("|") if d.strip()]

        records.append(
            GeneRecord(
                gene_id=str(gid),
                symbol=str(doc.get("name", "")),
                description=str(doc.get("description", "")),
                organism=str(org_name),
                chromosome=str(doc.get("chromosome", "")),
                map_location=str(doc.get("maplocation", "")),
                aliases=aliases,
                summary=str(doc.get("summary", "")),
                other_designations=designations,
            )
        )

    return records


def search_gene(
    query: str,
    *,
    organism: str | None = "Homo sapiens",
    limit: int = 10,
    http_client: HttpClient | None = None,
) -> list[GeneRecord]:
    """Search NCBI Gene by symbol, keyword, or ID and return GeneRecord objects."""
    client = http_client or get_default_client()
    q = query.strip()

    # If the user passed a pure numeric Entrez Gene ID, fetch directly
    if q.isdigit():
        return fetch_genes([q], http_client=client)

    org_clause = f' AND "{organism.strip()}"[Organism]' if organism else ""

    # If single token (likely a gene symbol like BRCA1 or TP53), try exact [Gene Name] first
    if re.match(r"^[A-Za-z0-9_-]+$", q):
        symbol_term = f'"{q}"[Gene Name] AND alive[prop]{org_clause}'
        sym_resp = client.get_json(
            _eutils_url("esearch.fcgi"),
            params={
                "db": "gene",
                "term": symbol_term,
                "retmax": limit,
                "sort": "relevance",
                "retmode": "json",
            },
        )
        sym_ids: list[str] = sym_resp.get("esearchresult", {}).get("idlist", [])
        if sym_ids:
            return fetch_genes(sym_ids, http_client=client)

    term = f"({q}) AND alive[prop]{org_clause}"

    search_resp = client.get_json(
        _eutils_url("esearch.fcgi"),
        params={
            "db": "gene",
            "term": term,
            "retmax": limit,
            "sort": "relevance",
            "retmode": "json",
        },
    )
    id_list: list[str] = search_resp.get("esearchresult", {}).get("idlist", [])
    if not id_list:
        return []
    return fetch_genes(id_list, http_client=client)


def search_ncbi(
    db: str,
    query: str,
    *,
    limit: int | None = 20,
    raw: bool = False,
    http_client: HttpClient | None = None,
) -> list[dict[str, Any]]:
    """Search any NCBI Entrez database and return clean record dictionaries.

    For `biosample`, `bioproject`, `nuccore`/`nucleotide`, `protein`, and `taxonomy` the raw
    esummary documents are flattened into structured records (see `BioSampleRecord`,
    `BioProjectRecord`, `NucleotideRecord`, `TaxonRecord`). Pass `raw=True` to get NCBI's
    original esummary dictionaries. `limit=None` returns every match (paginated).
    """
    client = http_client or get_default_client()
    db_clean = db.strip().lower()
    id_list, _ = esearch_ids(db_clean, query.strip(), limit=limit, http_client=client)
    if not id_list:
        return []
    result_block = esummary_docs(db_clean, id_list, http_client=client)
    docs = [result_block[u] for u in id_list if u in result_block]
    parser = _STRUCTURED_DBS.get(db_clean)
    if raw or parser is None:
        return docs
    return [parser(doc, db_clean).to_dict() for doc in docs]


def link_accession(
    accession: str,
    *,
    http_client: HttpClient | None = None,
) -> dict[str, Any]:
    """Find cross-database links for a GEO (GSE/GSM) or PubMed (PMID) accession.

    For a GSE: returns linked PubMed IDs, BioProject ID, platforms, sample count, and FTP URL.
    For a PMID: finds any GEO datasets (`db=gds`) citing or deposited by that paper!
    """
    client = http_client or get_default_client()
    acc = accession.strip().upper()

    if acc.startswith("GSE") or acc.startswith("GSM"):
        from PyNCBI.domains.geo import inspect_accession

        report = inspect_accession(
            acc,
            check_file_sizes=False,
            include_sample_breakdown=False,
            http_client=client,
        )
        linked_articles = (
            fetch_pubmed(report.pubmed_ids, http_client=client) if report.pubmed_ids else []
        )
        return {
            "accession": acc,
            "record_type": report.record_type,
            "title": report.title,
            "platforms": report.platforms,
            "sample_count": report.sample_count,
            "superseries_id": report.superseries_id,
            "subseries_ids": report.subseries_ids,
            "bioproject_id": report.bioproject_id,
            "sra_url": report.sra_url,
            "biosample_url": report.biosample_url,
            "pubmed_ids": report.pubmed_ids,
            "pubmed_articles": [a.to_dict() for a in linked_articles],
            "files": [f.to_dict() for f in report.files],
        }

    # Assume PMID (e.g., "PMID:27713574" or "27713574")
    pmid = acc.replace("PMID:", "").strip()
    geo_hits = search_geo(f"{pmid}[PubMed ID]", entry_type="gse", limit=25, http_client=client)
    articles = fetch_pubmed([pmid], http_client=client)
    return {
        "pmid": pmid,
        "article": articles[0].to_dict() if articles else None,
        "linked_geo_series": [h.to_dict() for h in geo_hits],
    }


def fetch_sra_runs(
    accession_or_query: str,
    *,
    limit: int | None = 500,
    save_to: str | Path | None = None,
    http_client: HttpClient | None = None,
) -> Table:
    """Fetch an NCBI SRA RunInfo table for any GSE, GSM, PRJNA, SRP, SRX, SRR, or search query.

    Automatically resolves GEO Series (GSE) or Sample (GSM) accessions to their
    linked BioProject/SRA identifiers before querying SRA.

    Args:
        accession_or_query: GSE, GSM, PRJNA, SRP, SRX, SRR accession, or Entrez SRA query
        limit: Maximum number of SRA experiments to fetch (default 500)
        save_to: Optional path (.csv, .tsv, .json) to save the RunInfo table
        http_client: Optional HTTP client

    Returns:
        Native table with an explicit `Run` field (SRR...) with sequencing metadata, layout, size_MB, and download_path
    """
    client = http_client or get_default_client()
    q = accession_or_query.strip()
    acc_upper = q.upper()
    reporter = get_reporter()

    with reporter.task(
        "SRA", "fetch_sra_runs", f"Fetching SRA RunInfo table for '{q}'", target=q
    ) as task:
        search_term = q
        if re.match(r"^GSE\d+$", acc_upper) or re.match(r"^GSM\d+$", acc_upper):
            from PyNCBI.domains.geo import inspect_accession

            report = inspect_accession(
                acc_upper,
                check_file_sizes=False,
                include_sample_breakdown=False,
                http_client=client,
            )
            sra_match = re.search(r"(SRP\d+|SRX\d+|SRS\d+|SRR\d+)", report.sra_url)
            if sra_match:
                search_term = sra_match.group(1)
            elif report.bioproject_id:
                search_term = report.bioproject_id
            else:
                search_term = acc_upper
            task.step(f"Resolved {acc_upper} -> SRA query '{search_term}'")

        id_list, total_count = esearch_ids("sra", search_term, limit=limit, http_client=client)
        if not id_list:
            task.complete(f"Found 0 SRA runs for '{q}'")
            return table()
        if total_count > len(id_list):
            reporter.warn(
                "SRA",
                f"Only {len(id_list)} of {total_count} SRA experiments fetched for '{q}'; use limit=None to get all",
                operation="fetch_sra_runs",
                target=q,
            )

        task.step(f"Fetching RunInfo CSV for {len(id_list)} SRA experiment(s)")
        frames: list[Table] = []
        for i in range(0, len(id_list), ESUMMARY_BATCH_SIZE):
            batch = id_list[i : i + ESUMMARY_BATCH_SIZE]
            csv_text = client.get_text(
                _eutils_url("efetch.fcgi"),
                params={
                    "db": "sra",
                    "id": ",".join(batch),
                    "rettype": "runinfo",
                    "retmode": "text",
                },
            )
            if csv_text.strip():
                frames.append(
                    parse_delimited_table(
                        csv_text.strip(), "runinfo.csv", id_field="Run", numeric=False
                    )
                )
        if not frames:
            task.complete(f"Found 0 SRA runs for '{q}'")
            return table()

        df = from_records(record for value in frames for record in iter_records(value))
        if "Run" in df["columns"]:
            df = from_records(
                (
                    record
                    for record in iter_records(df)
                    if record.get("Run") and record["Run"] != "Run"
                ),
                df["columns"],
            )
        if not df["rows"]:
            task.complete(f"Found 0 SRA runs for {q!r}")
            return df

        priority_cols = [
            "Run",
            "Experiment",
            "SampleName",
            "LibraryStrategy",
            "LibraryLayout",
            "LibrarySelection",
            "Platform",
            "Model",
            "spots",
            "bases",
            "size_MB",
            "avgLength",
            "BioProject",
            "SRAStudy",
            "BioSample",
            "ScientificName",
            "ReleaseDate",
            "download_path",
        ]
        ordered = [c for c in priority_cols if c in df["columns"]] + [
            c for c in df["columns"] if c not in priority_cols
        ]
        df = project(df, ordered)

        if save_to is not None and df["rows"]:
            save_data(df, save_to)
            task.step(f"Saved SRA RunInfo table to {save_to}")

        task.complete(f"Retrieved {len(df['rows'])} SRA run(s) for '{q}'")
        return df


def fetch_fasta(
    accessions: list[str] | str,
    *,
    db: str = "nuccore",
    seq_start: int | None = None,
    seq_stop: int | None = None,
    save_to: str | Path | None = None,
    http_client: HttpClient | None = None,
) -> list[FASTASequence]:
    """Fetch Nucleotide or Protein FASTA sequence(s) from NCBI Entrez (`efetch`).

    Args:
        accessions: Accession ID or list of IDs (e.g., 'NM_007294.4', 'NP_009225.1')
        db: 'nuccore' (nucleotide, default) or 'protein'
        seq_start: Optional 1-based start coordinate for sequence region slicing
        seq_stop: Optional 1-based stop coordinate for sequence region slicing
        save_to: Optional file path (.fasta, .fa, .txt) to save raw FASTA output
        http_client: Optional HTTP client

    Returns:
        List of FASTASequence objects
    """
    if isinstance(accessions, str):
        id_list = [a.strip() for a in re.split(r"[,\s]+", accessions) if a.strip()]
    else:
        id_list = [str(a).strip() for a in accessions if str(a).strip()]

    if not id_list:
        return []

    client = http_client or get_default_client()
    db_clean = db.strip().lower()
    if db_clean in ("nucleotide", "dna", "rna", "mrna"):
        db_clean = "nuccore"

    params: dict[str, Any] = {
        "db": db_clean,
        "id": ",".join(id_list),
        "rettype": "fasta",
        "retmode": "text",
    }
    if seq_start is not None:
        params["seq_start"] = seq_start
    if seq_stop is not None:
        params["seq_stop"] = seq_stop

    fasta_text = client.get_text(_eutils_url("efetch.fcgi"), params=params)
    if save_to is not None and fasta_text.strip():
        out_path = Path(save_to)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(fasta_text, encoding="utf-8")

    records: list[FASTASequence] = []
    raw_entries = [
        e.strip() for e in re.split(r"(?=^>)", fasta_text.strip(), flags=re.M) if e.strip()
    ]
    for entry in raw_entries:
        lines = entry.splitlines()
        if not lines or not lines[0].startswith(">"):
            continue
        header = lines[0][1:].strip()
        parts = header.split(None, 1)
        acc_id = parts[0] if parts else ""
        desc = parts[1] if len(parts) > 1 else header
        seq = "".join(ln.strip() for ln in lines[1:])
        records.append(
            FASTASequence(
                accession=acc_id,
                description=desc,
                db=db_clean,
                length=len(seq),
                sequence=seq,
                fasta_text=entry + "\n",
            )
        )

    return records
