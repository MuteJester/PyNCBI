"""Structured ClinVar & dbSNP variant lookup for PyNCBI.

Turns NCBI's raw, nested `esummary` payloads (35+ XML-derived keys) into compact
`ClinVarRecord` / `DbSNPRecord` objects that are readable by humans and LLM agents.

- `search_clinvar(query, significance=..., gene=...)`: gene symbol, rsID, HGVS, or free text
- `fetch_clinvar(ids)`: by ClinVar Variation ID(s) / VCV accession(s)
- `fetch_dbsnp(rsids)`: reference SNP records with position, alleles, genes, frequencies
- `fetch_variants(query, source='auto')`: one call that routes to ClinVar and/or dbSNP
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from PyNCBI.core.config import get_config
from PyNCBI.core.formatter import save_data
from PyNCBI.core.models import ClinVarRecord, DbSNPRecord
from PyNCBI.core.reporter import get_reporter
from PyNCBI.io.http_client import get_default_client

if TYPE_CHECKING:
    from PyNCBI.core.protocols import HttpClient

_RSID_RE = re.compile(r"^rs\d+$", re.I)
_GENE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9\-]{0,19}$")
_HGVS_RE = re.compile(r"^[A-Z]{1,2}_\d+(\.\d+)?\s*:\s*[cgpnmr]\.", re.I)
_VCV_RE = re.compile(r"^VCV0*(\d+)(\.\d+)?$", re.I)

# Partial, case-insensitive matches on ClinVar review-status wording -> star rating
_REVIEW_STARS: tuple[tuple[str, int], ...] = (
    ("practice guideline", 4),
    ("expert panel", 3),
    ("multiple submitters, no conflicts", 2),
    ("criteria provided", 1),
)


def _eutils(endpoint: str) -> str:
    return f"{get_config().NCBI_EUTILS_BASE_URL}/{endpoint}"


def review_status_to_stars(status: str) -> int:
    """Map a ClinVar review status string to its 0-4 star rating."""
    low = (status or "").lower()
    if "no assertion" in low:
        return 0
    for needle, stars in _REVIEW_STARS:
        if needle in low:
            return stars
    return 0


# ---------------------------------------------------------------------------
# Parsers (pure)
# ---------------------------------------------------------------------------


def _to_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _location(loc: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "chr": str(loc.get("chr", "")),
        "start": _to_int(loc.get("start")),
        "stop": _to_int(loc.get("stop")),
    }
    if loc.get("band"):
        out["band"] = str(loc["band"])
    return out


def parse_clinvar_summary(doc: dict[str, Any]) -> ClinVarRecord:
    """Flatten one `esummary db=clinvar` document into a `ClinVarRecord`."""
    variation = (
        (doc.get("variation_set") or [{}])[0] if isinstance(doc.get("variation_set"), list) else {}
    )
    genes = doc.get("genes") or []
    first_gene = genes[0] if genes and isinstance(genes[0], dict) else {}

    germline = doc.get("germline_classification") or {}
    # Older payloads expose `clinical_significance` directly
    legacy = doc.get("clinical_significance") or {}
    significance = str(germline.get("description") or legacy.get("description") or "")
    review = str(germline.get("review_status") or legacy.get("review_status") or "")
    last_eval = str(germline.get("last_evaluated") or legacy.get("last_evaluated") or "")
    if last_eval.startswith("1/01/01"):
        last_eval = ""
    last_eval = last_eval.replace(" 00:00", "").strip()

    conditions: list[str] = []
    xrefs: list[str] = []
    for trait in germline.get("trait_set") or legacy.get("trait_set") or []:
        if not isinstance(trait, dict):
            continue
        name = str(trait.get("trait_name", "")).strip()
        if name and name not in conditions:
            conditions.append(name)
        for xr in trait.get("trait_xrefs") or []:
            if isinstance(xr, dict) and xr.get("db_source") and xr.get("db_id"):
                ref = f"{xr['db_source']}:{xr['db_id']}"
                if ref not in xrefs:
                    xrefs.append(ref)

    rsid = ""
    for xr in variation.get("variation_xrefs") or []:
        if isinstance(xr, dict) and str(xr.get("db_source", "")).lower() == "dbsnp":
            rsid = f"rs{xr.get('db_id')}"
            break

    grch38: dict[str, Any] = {}
    grch37: dict[str, Any] = {}
    for loc in variation.get("variation_loc") or []:
        if not isinstance(loc, dict):
            continue
        asm = str(loc.get("assembly_name", ""))
        if asm.startswith("GRCh38") and not grch38:
            grch38 = _location(loc)
        elif asm.startswith("GRCh37") and not grch37:
            grch37 = _location(loc)

    subs = doc.get("supporting_submissions") or {}
    sub_count = len(subs.get("scv") or []) if isinstance(subs, dict) else 0

    return ClinVarRecord(
        variation_id=str(doc.get("uid", "")),
        accession=str(doc.get("accession", "")),
        title=str(doc.get("title", "")),
        gene_symbol=str(first_gene.get("symbol") or doc.get("gene_sort") or ""),
        gene_id=str(first_gene.get("geneid", "")),
        variant_type=str(variation.get("variant_type") or doc.get("obj_type") or ""),
        cdna_change=str(variation.get("cdna_change", "")),
        protein_change=str(doc.get("protein_change", "")),
        molecular_consequences=[str(m) for m in doc.get("molecular_consequence_list") or []],
        clinical_significance=significance,
        review_status=review,
        review_stars=review_status_to_stars(review),
        last_evaluated=last_eval,
        conditions=conditions,
        condition_xrefs=xrefs,
        rsid=rsid,
        canonical_spdi=str(variation.get("canonical_spdi", "")),
        grch38=grch38,
        grch37=grch37,
        oncogenicity=str((doc.get("oncogenicity_classification") or {}).get("description", "")),
        clinical_impact=str(
            (doc.get("clinical_impact_classification") or {}).get("description", "")
        ),
        submission_count=sub_count,
    )


def parse_dbsnp_summary(doc: dict[str, Any]) -> DbSNPRecord:
    """Flatten one `esummary db=snp` document into a `DbSNPRecord`."""
    rsid = f"rs{doc.get('snp_id') or doc.get('uid')}"
    spdi = str(doc.get("spdi", ""))
    ref = ""
    alts: list[str] = []
    for part in spdi.split(","):
        bits = part.strip().split(":")
        if len(bits) == 4:
            ref = ref or bits[2]
            if bits[3] and bits[3] not in alts:
                alts.append(bits[3])

    def _pos(key: str) -> int | None:
        val = str(doc.get(key, ""))
        return _to_int(val.split(":")[-1]) if ":" in val else None

    pos38 = _pos("chrpos")
    if pos38 is None and spdi:
        first = spdi.split(",")[0].split(":")
        base = _to_int(first[1]) if len(first) == 4 else None
        if base is not None:
            pos38 = base + 1  # SPDI is 0-based

    hgvs: list[str] = []
    protein_change = ""
    for field in str(doc.get("docsum", "")).split("|"):
        if field.startswith("HGVS="):
            hgvs = [h.strip() for h in field[5:].split(",") if h.strip()]
    for h in hgvs:
        if ":p." in h:
            protein_change = h.split(":", 1)[1]
            break

    freqs: dict[str, float] = {}
    for item in doc.get("global_mafs") or []:
        if not isinstance(item, dict):
            continue
        m = re.match(r"^[ACGTN\-]+=([0-9.eE\-]+)/", str(item.get("freq", "")))
        if m:
            try:
                freqs[str(item.get("study"))] = float(m.group(1))
            except ValueError:
                continue

    genes = [g for g in doc.get("genes") or [] if isinstance(g, dict)]
    return DbSNPRecord(
        rsid=rsid,
        chromosome=str(doc.get("chr", "")),
        position_grch38=pos38,
        position_grch37=_pos("chrpos_prev_assm"),
        spdi=spdi,
        ref_allele=ref,
        alt_alleles=alts,
        gene_symbols=[str(g.get("name", "")) for g in genes if g.get("name")],
        gene_ids=[str(g.get("gene_id", "")) for g in genes if g.get("gene_id")],
        functional_class=[f for f in str(doc.get("fxn_class", "")).split(",") if f],
        clinical_significance=[
            c for c in str(doc.get("clinical_significance", "")).split(",") if c
        ],
        hgvs=hgvs,
        protein_change=protein_change,
        allele_frequencies=freqs,
        validated=[v for v in str(doc.get("validated", "")).split(",") if v],
    )


# ---------------------------------------------------------------------------
# Query building & network
# ---------------------------------------------------------------------------


def build_clinvar_term(
    query: str, *, significance: str | None = None, gene: str | None = None
) -> str:
    """Build an Entrez ClinVar search term from a gene symbol, rsID, HGVS string, or text."""
    q = query.strip()
    if _RSID_RE.match(q):
        term = f"{q.lower()}[rs]"
    elif _HGVS_RE.match(q):
        # Exact HGVS match: ClinVar's `[varname]` field needs the expression quoted
        term = f'"{q.replace(" ", "")}"[varname]'
    elif _GENE_RE.match(q) and not q.isdigit():
        term = f"{q.upper()}[gene]"
    else:
        term = f"({q})"
    if gene and f"{gene.strip().upper()}[gene]" != term:
        term += f" AND {gene.strip().upper()}[gene]"
    if significance:
        term += f' AND "{significance.strip().lower()}"[clinsig]'
    return term


def fetch_clinvar(
    ids: list[str] | str,
    *,
    http_client: HttpClient | None = None,
) -> list[ClinVarRecord]:
    """Fetch ClinVar records by Variation ID(s) (numeric) or VCV accession(s)."""
    raw = re.split(r"[,\s]+", ids) if isinstance(ids, str) else [str(i) for i in ids]
    uids: list[str] = []
    for item in raw:
        item = item.strip()
        if not item:
            continue
        m = _VCV_RE.match(item)
        uids.append(m.group(1) if m else item)
    if not uids:
        return []
    client = http_client or get_default_client()
    payload = client.get_json(
        _eutils("esummary.fcgi"), params={"db": "clinvar", "id": ",".join(uids), "retmode": "json"}
    )
    result = payload.get("result", {})
    return [parse_clinvar_summary(result[u]) for u in uids if isinstance(result.get(u), dict)]


def search_clinvar(
    query: str,
    *,
    significance: str | None = None,
    gene: str | None = None,
    limit: int | None = 20,
    save_to: str | None = None,
    http_client: HttpClient | None = None,
) -> list[ClinVarRecord]:
    """Search ClinVar and return clean ClinVarRecord objects.

    Args:
        query: Gene symbol ('BRCA1'), rsID ('rs80357906'), HGVS ('NM_007294.4:c.5266dupC'),
            VCV accession, or free text ('BRCA1 frameshift')
        significance: Optional clinical significance filter ('pathogenic', 'likely pathogenic',
            'benign', 'uncertain significance', ...)
        gene: Optional gene symbol restriction (for free-text queries)
        limit: Maximum number of variants (default 20)
        save_to: Optional path (.csv, .tsv, .json) to save the records
        http_client: Optional HTTP client
    """
    client = http_client or get_default_client()
    q = query.strip()
    if _VCV_RE.match(q):
        return fetch_clinvar([q], http_client=client)
    term = build_clinvar_term(q, significance=significance, gene=gene)
    with get_reporter().task(
        "CLINVAR", "search_clinvar", f"Searching ClinVar: {term}", target=q
    ) as task:
        from PyNCBI.domains.entrez import esearch_ids

        ids, total = esearch_ids("clinvar", term, limit=limit, http_client=client)
        if not ids:
            task.complete(f"0 ClinVar variants for '{term}'")
            return []
        records = fetch_clinvar(ids, http_client=client)
        if save_to and records:
            save_data([r.to_dict() for r in records], save_to)
        task.complete(f"{len(records)} ClinVar variant(s) returned (of {total} matching '{term}')")
        return records


def fetch_dbsnp(
    rsids: list[str] | str,
    *,
    http_client: HttpClient | None = None,
) -> list[DbSNPRecord]:
    """Fetch dbSNP reference SNP records by rsID(s) (e.g. 'rs7412' or '7412')."""
    raw = re.split(r"[,\s]+", rsids) if isinstance(rsids, str) else [str(i) for i in rsids]
    uids = [r.strip().lower().removeprefix("rs") for r in raw if r.strip()]
    uids = [u for u in uids if u.isdigit()]
    if not uids:
        return []
    client = http_client or get_default_client()
    payload = client.get_json(
        _eutils("esummary.fcgi"), params={"db": "snp", "id": ",".join(uids), "retmode": "json"}
    )
    result = payload.get("result", {})
    return [parse_dbsnp_summary(result[u]) for u in uids if isinstance(result.get(u), dict)]


def fetch_variants(
    query: str,
    *,
    source: str = "auto",
    significance: str | None = None,
    gene: str | None = None,
    limit: int = 20,
    http_client: HttpClient | None = None,
) -> dict[str, Any]:
    """Look up a gene / rsID / HGVS / VCV in ClinVar and/or dbSNP in one call.

    `source='auto'` queries ClinVar always and dbSNP additionally when the query is an rsID.
    Returns `{'query', 'source', 'clinvar': [...], 'dbsnp': [...]}` with record dicts.
    """
    client = http_client or get_default_client()
    src = source.strip().lower()
    if src not in ("auto", "clinvar", "dbsnp"):
        raise ValueError("source must be 'auto', 'clinvar', or 'dbsnp'")
    q = query.strip()
    is_rs = bool(_RSID_RE.match(q))
    out: dict[str, Any] = {"query": q, "source": src, "clinvar": [], "dbsnp": []}
    if src in ("auto", "clinvar"):
        out["clinvar"] = [
            r.to_dict()
            for r in search_clinvar(
                q, significance=significance, gene=gene, limit=limit, http_client=client
            )
        ]
    if src == "dbsnp" or (src == "auto" and is_rs):
        if not is_rs:
            raise ValueError("dbSNP lookups require an rsID (e.g. 'rs7412')")
        out["dbsnp"] = [r.to_dict() for r in fetch_dbsnp(q, http_client=client)]
    return out


def format_variants_markdown(result: dict[str, Any]) -> str:
    """Render a `fetch_variants` result as compact Markdown tables."""
    lines = [f"# Variant lookup: `{result['query']}`"]
    cv = result.get("clinvar") or []
    if cv:
        lines.append(f"\n## ClinVar ({len(cv)} variant(s))")
        lines.append(
            "| Accession | Title | Gene | Significance | Review | Conditions | GRCh38 | rsID |"
        )
        lines.append("| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |")
        for r in cv:
            stars = "★" * int(r.get("review_stars", 0)) or "–"
            lines.append(
                f"| {r['accession']} | {r['title'][:70]} | {r['gene_symbol']} | {r['clinical_significance']} | "
                f"{stars} | {'; '.join(r['conditions'][:2])[:80]} | {r.get('grch38_location', '')} | {r['rsid']} |"
            )
    else:
        lines.append("\n_No ClinVar records._")
    snps = result.get("dbsnp") or []
    if snps:
        lines.append(f"\n## dbSNP ({len(snps)} record(s))")
        for s in snps:
            lines.append(
                f"- **{s['rsid']}** chr{s['chromosome']}:{s['position_grch38']} {s['ref_allele']}>{','.join(s['alt_alleles'])} "
                f"| genes: {', '.join(s['gene_symbols'])} | {', '.join(s['functional_class'][:2])} | "
                f"clinical: {', '.join(s['clinical_significance'][:4])} | {s['protein_change']}"
            )
            top = sorted(s["allele_frequencies"].items(), key=lambda kv: kv[0])[:4]
            if top:
                lines.append("  - MAF: " + ", ".join(f"{k}={v:.4f}" for k, v in top))
    return "\n".join(lines)
